"""A replay-safe bulk adopter scripted like a chat provider's add-members call.

The provider is idempotent (adding an existing member is a no-op), answers all
or nothing per call except for per-user rejections, and throttles whole calls
with a reset time. Its member set is the independent acceptance ledger.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar, Literal

import pytest
from django.conf import settings
from django.db import connection, models
from django.test.utils import isolate_apps, override_settings
from pydantic import BaseModel

from due.bulk import BulkOutcome, BulkOutcomeState, BulkResult, BulkRetryPolicy, RejectionResponse
from due.django.capabilities.bulk import BulkEffectWork, DurableBulkEffect
from due.django.public import DurableWorkPolicy, DurableWorkState, durable_work_schema
from due.django.runtime.coordination import ContinueAfter
from due.django.runtime.registry import DurableWorkRegistration, recovery_schedules
from due.errors import EffectRejection
from tests.harness.primitive import PrimitiveOwner
from tests.harness.registrations import durable_work_installed

with isolate_apps("tests"):

    class MemberSyncWork(BulkEffectWork):
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(PrimitiveOwner, on_delete=models.SET_NULL, null=True)

        class Meta:
            app_label = "tests"
            indexes, constraints = durable_work_schema(name_prefix="member_sync")


class Membership(BaseModel):
    status: Literal["member", "rejected"]


class RateLimited(EffectRejection):
    """The provider refused the whole call until its rate-limit window resets."""

    reset_seconds: int


ProviderReply = Exception | dict[str, Literal["retry", "reject"]]
"""One scripted call: raise, or answer with the members that were not added."""


@dataclass(frozen=True)
class MemberSync(DurableBulkEffect[PrimitiveOwner, MemberSyncWork, Membership]):
    result_type: type[Membership] = field(default=Membership, kw_only=True)
    bulk_policy: BulkRetryPolicy = field(default=BulkRetryPolicy(3, timedelta(seconds=30)), kw_only=True)
    members: ClassVar[set[str]] = set()
    calls: ClassVar[list[tuple[str, ...]]] = []
    script: ClassVar[list[ProviderReply]] = []
    apply_then_lose_reply: ClassVar[bool] = False

    def prepare_batch(self, work: MemberSyncWork, keys: tuple[str, ...]) -> None:
        return None

    def send_batch(self, work: MemberSyncWork, keys: tuple[str, ...], payload: None) -> BulkResult[Membership]:
        self.calls.append(keys)
        reply = self.script.pop(0) if self.script else {}
        if isinstance(reply, Exception):
            raise reply
        added = [key for key in keys if key not in reply]
        self.members.update(added)  # Idempotent: a resent member is not added twice.
        if self.apply_then_lose_reply:
            MemberSync.apply_then_lose_reply = False
            raise TimeoutError("the provider applied the call but its reply was lost")
        return BulkResult(
            items=tuple(
                BulkOutcome(key=key, state=BulkOutcomeState.COMPLETE, result=Membership(status="member"))
                if key not in reply
                else BulkOutcome(key=key, state=BulkOutcomeState.RETRYABLE)
                if reply[key] == "retry"
                else BulkOutcome(key=key, state=BulkOutcomeState.COMPLETE, result=Membership(status="rejected"))
                for key in keys
            )
        )

    def on_batch_rejected(self, work: MemberSyncWork, rejection: EffectRejection) -> RejectionResponse | ContinueAfter:
        if isinstance(rejection, RateLimited):
            return ContinueAfter(timedelta(seconds=rejection.reset_seconds), until=None)
        return RejectionResponse.RETRY_LATER

    def finalize_batch(self, work: MemberSyncWork, result: BulkResult[Membership]) -> None:
        joined = sorted(
            item.key for item in self.completed_items(work) if item.result and item.result.status == "member"
        )
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value=",".join(joined))

    def finalize_retry(self, work: MemberSyncWork, error: Exception) -> None:
        if work.state == DurableWorkState.EXHAUSTED:
            PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="exhausted")

    def finalize_terminal(self, work: MemberSyncWork, error: Exception) -> None:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="terminal")


member_sync = MemberSync.for_owner(
    owner=PrimitiveOwner,
    work=MemberSyncWork,
    input_fields=("universe", "bulk_keys"),
    policy=DurableWorkPolicy(retention=None, retry_delay=timedelta(seconds=1), max_rearms=2),
)


def admit_cohort(owner_id: int, keys: tuple[str, ...] = ("a", "b", "c")) -> MemberSyncWork | None:
    """The producer: admit one fixed cohort for an owner, once."""
    with member_sync.atomic(owner_id, notify_worker=False) as pending:
        if pending.existing_work is not None:
            return pending.existing_work
        return pending.request_work(MemberSyncWork(bulk_keys=list(keys), universe="acme"))


def record_sync(keys: tuple[str, ...] = ("a", "b", "c")) -> MemberSyncWork:
    work = admit_cohort(PrimitiveOwner.objects.create().pk, keys)
    assert work is not None, "a new owner always admits its cohort"
    return work


@contextmanager
def registered_member_sync() -> Iterator[None]:
    entry = DurableWorkRegistration(
        name="member_sync_self_test", capability=member_sync, cadence_seconds=60, queue="celery"
    )
    with (
        durable_work_installed(entry),
        override_settings(CELERY_BEAT_SCHEDULE={**settings.CELERY_BEAT_SCHEDULE, **recovery_schedules([entry])}),
    ):
        yield


@pytest.fixture(name="member_sync_operation")
def member_sync_environment(isolated_primitive: None) -> Iterator[MemberSync]:
    MemberSync.members.clear()
    MemberSync.calls.clear()
    MemberSync.script.clear()
    MemberSync.apply_then_lose_reply = False
    with connection.schema_editor() as editor:
        editor.create_model(MemberSyncWork)
    try:
        with registered_member_sync():
            yield member_sync
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(MemberSyncWork)
