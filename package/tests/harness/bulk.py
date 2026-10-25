"""Real work/evidence tables and an independently scripted batch provider."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar

import pytest
from django.conf import settings
from django.db import connection, models
from django.test.utils import isolate_apps, override_settings
from pydantic import BaseModel

from due.bulk import BulkOutcome, BulkOutcomeState, BulkResult, BulkRetryPolicy, RejectionResponse
from due.django.capabilities.bulk import (
    BulkExternalOperationWork,
    CapturedBatch,
    DurableBulkExternalOperation,
    StoredBulkEffectEvidence,
    bulk_evidence_index,
)
from due.django.models import EffectAttempt
from due.django.public import DurableWorkPolicy, DurableWorkState, durable_work_schema
from due.django.runtime.coordination import ContinueAfter
from due.django.runtime.registry import DurableWorkRegistration, recovery_schedules
from due.errors import EffectRejection
from tests.harness.primitive import PrimitiveOwner
from tests.harness.registrations import durable_work_installed

with isolate_apps("tests"):

    class BatchWork(BulkExternalOperationWork):
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(PrimitiveOwner, on_delete=models.SET_NULL, null=True)

        class Meta:
            app_label = "tests"
            indexes, constraints = durable_work_schema(name_prefix="batch")

    class BatchReceipt(StoredBulkEffectEvidence):
        class Meta:
            app_label = "tests"
            indexes = [bulk_evidence_index("batch_receipt_pending")]


class ItemResult(BaseModel):
    message: str


@dataclass(frozen=True)
class BatchOperation(DurableBulkExternalOperation[PrimitiveOwner, BatchWork, ItemResult]):
    receipt_model = BatchReceipt
    result_type: type[ItemResult] = field(default=ItemResult, kw_only=True)
    bulk_policy: BulkRetryPolicy = field(default=BulkRetryPolicy(3, timedelta(seconds=30)), kw_only=True)
    calls: ClassVar[list[tuple[str, ...]]] = []
    script: ClassVar[list[BulkResult[ItemResult] | Exception]] = []
    accepted: ClassVar[list[str]] = []
    requests: ClassVar[list[tuple[int, tuple[str, ...]]]] = []
    failure: ClassVar[Exception | None] = None
    prepare_failure: ClassVar[Exception | None] = None
    lose_reply: ClassVar[bool] = False
    accepted_by_work: ClassVar[dict[int, list[str]]] = {}
    rejection_response: ClassVar[RejectionResponse | ContinueAfter] = RejectionResponse.RETRY_LATER
    published_rejections: ClassVar[list[str]] = []

    def prepare_batch(self, work: BatchWork, batch: CapturedBatch) -> None:
        if self.prepare_failure is not None:
            raise self.prepare_failure

    def send_batch(self, work: BatchWork, batch: CapturedBatch, payload: None) -> BulkResult[ItemResult]:
        self.calls.append(batch.keys)
        self.requests.append((work.pk, batch.keys))
        if self.failure is not None:
            raise self.failure
        response = self.script.pop(0) if self.script else result(*batch.keys)
        if isinstance(response, Exception):
            raise response
        accepted = [item.key for item in response.items if item.state is BulkOutcomeState.COMPLETE]
        self.accepted.extend(accepted)
        self.accepted_by_work.setdefault(work.pk, []).extend(accepted)
        if self.lose_reply:
            raise TimeoutError("provider accepted the batch but its reply was lost")
        return response

    def finalize_batch(self, work: BatchWork, result: BulkResult[ItemResult]) -> None:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(
            value=",".join(item.key for item in self.completed_items(work))
        )

    def on_batch_rejected(self, work: BatchWork, rejection: EffectRejection) -> RejectionResponse | ContinueAfter:
        return self.rejection_response

    def finalize_rejection(self, work: BatchWork, rejection: EffectRejection) -> None:
        self.published_rejections.append(rejection.message)

    def finalize_unknown(self, work: BatchWork) -> None:
        accepted = ",".join(item.key for item in self.completed_items(work))
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value=accepted or "unknown")

    def finalize_retry(self, work: BatchWork, error: Exception) -> None:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(
            value="exhausted" if work.state == DurableWorkState.EXHAUSTED else "retry"
        )

    def finalize_terminal(self, work: BatchWork, error: Exception) -> None:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="terminal")


@dataclass(frozen=True)
class ObservedBatchOperation(BatchOperation):
    """A provider whose uncertain calls are resolved by later lookup, e.g. an async task status."""

    observations: ClassVar[list[BulkResult[ItemResult]]] = []

    def observe_batch(
        self, work: BatchWork, attempt: EffectAttempt, members: tuple[str, ...]
    ) -> BulkResult[ItemResult] | None:
        return self.observations.pop(0) if self.observations else None


batch = BatchOperation.for_owner(
    owner=PrimitiveOwner,
    work=BatchWork,
    input_fields=("universe", "bulk_keys"),
    policy=DurableWorkPolicy(retention=None, retry_delay=timedelta(seconds=1), max_rearms=2),
)


@contextmanager
def registered_batch() -> Iterator[None]:
    entry = DurableWorkRegistration(name="batch_self_test", capability=batch, cadence_seconds=60, queue="celery")
    with (
        durable_work_installed(entry),
        override_settings(CELERY_BEAT_SCHEDULE={**settings.CELERY_BEAT_SCHEDULE, **recovery_schedules([entry])}),
    ):
        yield


def result(*keys: str, state: BulkOutcomeState = BulkOutcomeState.COMPLETE) -> BulkResult[ItemResult]:
    return BulkResult(items=tuple(BulkOutcome(key=key, state=state, result=ItemResult(message=key)) for key in keys))


def record(capability: BatchOperation, keys: tuple[str, ...] = ("a", "b", "c")) -> BatchWork:
    with capability.atomic(PrimitiveOwner.objects.create().pk, notify_worker=False) as pending:
        return pending.request_work(BatchWork(bulk_keys=list(keys), universe="acme"))


@pytest.fixture(name="batch_operation")
def batch_environment(isolated_primitive: None) -> Iterator[BatchOperation]:
    BatchOperation.calls.clear()
    BatchOperation.script.clear()
    BatchOperation.accepted.clear()
    BatchOperation.requests.clear()
    BatchOperation.failure = None
    BatchOperation.prepare_failure = None
    BatchOperation.rejection_response = RejectionResponse.RETRY_LATER
    BatchOperation.published_rejections.clear()
    ObservedBatchOperation.observations.clear()
    BatchOperation.lose_reply = False
    BatchOperation.accepted_by_work.clear()
    with connection.schema_editor() as editor:
        editor.create_model(BatchWork)
        editor.create_model(BatchReceipt)
    try:
        with registered_batch():
            yield batch
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(BatchReceipt)
            editor.delete_model(BatchWork)


observed_batch = ObservedBatchOperation.for_owner(
    owner=PrimitiveOwner,
    work=BatchWork,
    input_fields=("universe", "bulk_keys"),
    policy=DurableWorkPolicy(retention=None, retry_delay=timedelta(seconds=1), max_rearms=2),
)
