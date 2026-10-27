"""Business intent and Due coordination share a UUID row and real SQL storage."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar, cast
from uuid import UUID, uuid7

import pytest
from django.conf import settings
from django.db import connection, models
from django.test import override_settings
from django.test.utils import isolate_apps

from due.bulk import BulkOutcomeState, BulkResult
from due.django.capabilities.bulk import BulkExternalOperationWork, CapturedBatch, DurableBulkExternalOperation
from due.django.public import DurableWorkPolicy, DurableWorkState, durable_work_schema
from due.django.runtime.registry import DurableWorkRegistration, recovery_schedules
from tests.harness.bulk import BatchReceipt, ItemResult, result
from tests.harness.registrations import durable_work_installed

with isolate_apps("tests"):

    class Chunk(BulkExternalOperationWork):
        id = models.UUIDField(primary_key=True, default=uuid7, editable=False)  # pyrefly: ignore[bad-override-mutable-attribute]
        delivered = models.JSONField(default=list)
        product_status = models.CharField(max_length=40, default="pending")
        state = models.CharField(
            max_length=24, choices=[*DurableWorkState.choices, ("legacy", "legacy")], default=DurableWorkState.READY
        )
        work_external_states: ClassVar[dict[str, str]] = {
            "legacy": "The legacy producer owns these rows; the coexistence control proves Due never executes them."
        }

        class Meta:
            app_label = "tests"
            indexes = durable_work_schema("self_chunk")[0]
            constraints = [
                models.CheckConstraint(
                    name=constraint.name,
                    condition=models.Q(state="legacy") | models.Q(cast(models.CheckConstraint, constraint).condition),
                )
                for constraint in durable_work_schema("self_chunk")[1]
            ]


@dataclass(frozen=True)
class SendChunk(DurableBulkExternalOperation[Chunk, Chunk, ItemResult]):
    receipt_model = BatchReceipt
    result_type: type[ItemResult] = field(default=ItemResult, kw_only=True)
    calls: ClassVar[list[tuple[UUID, tuple[str, ...]]]] = []
    lose_reply: ClassVar[bool] = False
    failure: ClassVar[Exception | None] = None
    script: ClassVar[list[BulkResult[ItemResult] | Exception]] = []
    accepted_by_work: ClassVar[dict[UUID, list[str]]] = {}

    def prepare_batch(self, work: Chunk, batch: CapturedBatch) -> None:
        pass

    def send_batch(self, work: Chunk, batch: CapturedBatch, payload: None) -> BulkResult[ItemResult]:
        self.calls.append((work.pk, batch.keys))
        if self.failure is not None:
            raise self.failure
        response = self.script.pop(0) if self.script else result(*batch.keys)
        if isinstance(response, Exception):
            raise response
        self.accepted_by_work.setdefault(work.pk, []).extend(
            item.key for item in response.items if item.state is BulkOutcomeState.COMPLETE
        )
        if self.lose_reply:
            raise TimeoutError("provider accepted; reply lost")
        return response

    def finalize_batch(self, work: Chunk, result: BulkResult[ItemResult]) -> None:
        work.delivered = [item.key for item in self.completed_items(work)]
        work.product_status = ",".join(work.delivered)
        work.save(update_fields=["delivered", "product_status"])

    def finalize_retry(self, work: Chunk, error: Exception) -> None:
        Chunk.objects.filter(pk=work.pk).update(product_status="retry")

    def finalize_terminal(self, work: Chunk, error: Exception) -> None:
        Chunk.objects.filter(pk=work.pk).update(product_status="terminal")

    def finalize_unknown(self, work: Chunk) -> None:
        Chunk.objects.filter(pk=work.pk).update(
            product_status=",".join(item.key for item in self.completed_items(work)) or "unknown"
        )


chunk_delivery = SendChunk.for_work(
    work=Chunk,
    input_fields=("universe", "bulk_keys"),
    policy=DurableWorkPolicy(retention=None, retry_delay=timedelta(seconds=1), max_rearms=2),
)


@contextmanager
def registered_chunk() -> Iterator[None]:
    entry = DurableWorkRegistration(
        name="chunk_self_test", capability=chunk_delivery, cadence_seconds=60, queue="celery"
    )
    with (
        durable_work_installed(entry),
        override_settings(CELERY_BEAT_SCHEDULE={**settings.CELERY_BEAT_SCHEDULE, **recovery_schedules([entry])}),
    ):
        yield


@pytest.fixture(name="self_chunk")
def chunk_environment(isolated_primitive: None) -> Iterator[SendChunk]:
    SendChunk.calls.clear()
    SendChunk.script.clear()
    SendChunk.accepted_by_work.clear()
    SendChunk.lose_reply = False
    SendChunk.failure = None
    with connection.schema_editor() as editor:
        editor.create_model(Chunk)
        editor.create_model(BatchReceipt)
    try:
        with registered_chunk():
            yield chunk_delivery
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(BatchReceipt)
            editor.delete_model(Chunk)
