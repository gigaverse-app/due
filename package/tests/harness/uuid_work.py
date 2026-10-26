"""UUID work rows retain the same admission, transport and evidence lifecycle."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import ClassVar
from uuid import UUID, uuid4

import pytest
from django.conf import settings
from django.db import connection, models
from django.test.utils import isolate_apps, override_settings

from due.django.capabilities.operations import DurableExternalOperation, ExternalOperationWork
from due.django.evidence.effect_evidence import JsonAppliedEffectEvidenceStore
from due.django.public import DurableWorkPolicy, Outcome, StatusMirror, durable_work_schema
from due.django.runtime.registry import DurableWorkRegistration, recovery_schedules
from due.errors import EffectNotAppliedError
from tests.harness.operation import ProviderResult, Receipt
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.registrations import durable_work_installed

with isolate_apps("tests"):

    class UUIDWork(ExternalOperationWork):
        # Django permits concrete PKs on abstract models; Pyrefly assumes an implicit int id.
        id = models.UUIDField(primary_key=True, default=uuid4, editable=False)  # pyrefly: ignore[bad-override-mutable-attribute]
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(PrimitiveOwner, on_delete=models.SET_NULL, null=True)
        payload = models.CharField(max_length=40)

        class Meta:
            app_label = "tests"
            indexes, constraints = durable_work_schema("uuid_work")


class UUIDPublication(DurableExternalOperation[PrimitiveOwner, UUIDWork, ProviderResult]):
    evidence_store = JsonAppliedEffectEvidenceStore(Receipt, ProviderResult)
    accepted: ClassVar[list[UUID]] = []
    lose_reply: ClassVar[bool] = False
    failure: ClassVar[EffectNotAppliedError | None] = None
    calls: ClassVar[list[UUID]] = []
    mirror = StatusMirror(lambda work: PrimitiveOwner.objects.filter(pk=work.owner_id))
    on_terminal: Outcome[UUIDWork, Exception] = mirror.outcome({"value": "terminal"})
    on_unknown: Outcome[UUIDWork, None] = mirror.outcome({"value": "unknown"})
    on_retry: Outcome[UUIDWork, Exception] = mirror.outcome({"value": "retry"})

    def perform_effect(self, work: UUIDWork) -> ProviderResult:
        self.calls.append(work.pk)
        if self.failure is not None:
            raise self.failure
        self.accepted.append(work.pk)
        if self.lose_reply:
            raise TimeoutError("accepted but reply lost")
        return ProviderResult(value=work.payload)

    def finalize_applied(self, work: UUIDWork, result: ProviderResult) -> None:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value=result.value)


uuid_capability = UUIDPublication.for_owner(
    work=UUIDWork, policy=DurableWorkPolicy(retention=None, retry_delay=timedelta(seconds=1))
)


@contextmanager
def registered_uuid() -> Iterator[None]:
    entry = DurableWorkRegistration(
        name="uuid_work_test", capability=uuid_capability, cadence_seconds=60, queue="celery"
    )
    with (
        durable_work_installed(entry),
        override_settings(CELERY_BEAT_SCHEDULE={**settings.CELERY_BEAT_SCHEDULE, **recovery_schedules([entry])}),
    ):
        yield


@pytest.fixture
def uuid_publication(isolated_primitive: None) -> Iterator[UUIDPublication]:
    UUIDPublication.accepted.clear()
    UUIDPublication.calls.clear()
    UUIDPublication.failure = None
    UUIDPublication.lose_reply = False
    with connection.schema_editor() as editor:
        editor.create_model(UUIDWork)
        editor.create_model(Receipt)
    try:
        with registered_uuid():
            yield uuid_capability
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(Receipt)
            editor.delete_model(UUIDWork)


def admit(capability: UUIDPublication, *, notify: bool = False) -> UUIDWork:
    with capability.atomic(PrimitiveOwner.objects.create().pk, notify_worker=notify) as pending:
        return pending.request_work(UUIDWork(universe="acme", payload="published"))
