"""A real uncertain operation and provider observation, registered on isolated SQL tables."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import ClassVar

import pytest
from django.db import connection, models
from django.test.utils import isolate_apps

from due.django.capabilities.observed_operations import (
    ObservedExternalOperation,
    ObservedExternalOperationWork,
    observation_index,
)
from due.django.evidence.effect_evidence import JsonAppliedEffectEvidenceStore
from due.django.integration import current_tenant
from due.django.models import durable_work_schema
from due.django.public import DurableWorkPolicy
from due.django.runtime.registry import DurableWorkRegistration
from due.django.testing import make_observation_due
from due.errors import EffectOutcomeUnknownError
from tests.harness.operation import ProviderResult, Receipt, mirror
from tests.harness.primitive import PrimitiveOwner, primitive_tables
from tests.harness.registrations import durable_work_installed

with isolate_apps("tests"):

    class ObservedWork(ObservedExternalOperationWork):
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(PrimitiveOwner, on_delete=models.SET_NULL, null=True)
        payload = models.CharField(max_length=40)

        class Meta:
            app_label = "tests"
            indexes, constraints = durable_work_schema("observed_test")
            indexes.append(observation_index("observed_test_observe_ix"))


class ObservationProvider:
    creates: ClassVar[list[int]] = []
    reads: ClassVar[list[tuple[int, str]]] = []
    result: ClassVar[ProviderResult | None] = None
    failure: ClassVar[Exception | None] = None

    @classmethod
    def observe(cls, work_id: int) -> ProviderResult | None:
        cls.reads.append((work_id, current_tenant()))
        if cls.failure is not None:
            raise cls.failure
        return cls.result


class ObservedPublication(ObservedExternalOperation[PrimitiveOwner, ObservedWork, ProviderResult]):
    observation_interval = timedelta(seconds=30)
    evidence_store = JsonAppliedEffectEvidenceStore(Receipt, ProviderResult)
    on_applied = mirror.outcome(lambda work, result: {"value": result.value})

    def perform_effect(self, work: ObservedWork) -> ProviderResult:
        ObservationProvider.creates.append(work.pk)
        raise TimeoutError("accepted remotely, response lost")

    def observe_effect(self, work: ObservedWork) -> ProviderResult | None:
        return ObservationProvider.observe(work.pk)


@contextmanager
def registered_observation(capability: ObservedPublication) -> Iterator[None]:
    registration = DurableWorkRegistration(name="observation_self_test", capability=capability, queue="observations")
    with durable_work_installed(registration):
        yield


@pytest.fixture
def observer(transactional_db: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[ObservedPublication]:
    monkeypatch.setattr(ObservationProvider, "creates", [])
    monkeypatch.setattr(ObservationProvider, "reads", [])
    monkeypatch.setattr(ObservationProvider, "result", None)
    monkeypatch.setattr(ObservationProvider, "failure", None)
    with primitive_tables():
        with connection.schema_editor() as editor:
            editor.create_model(ObservedWork)
            editor.create_model(Receipt)
        capability = ObservedPublication.for_owner(
            owner=PrimitiveOwner,
            work=ObservedWork,
            input_fields=("universe", "payload"),
            policy=DurableWorkPolicy(retention=None, batch_size=2),
        )
        try:
            with registered_observation(capability):
                yield capability
        finally:
            with connection.schema_editor() as editor:
                editor.delete_model(Receipt)
                editor.delete_model(ObservedWork)


def uncertain_work(capability: ObservedPublication, *, universe: str = "acme") -> ObservedWork:
    with capability.atomic(PrimitiveOwner.objects.create().pk, notify_worker=False) as pending:
        work = pending.request_work(ObservedWork(payload="applied", universe=universe))
    with pytest.raises(EffectOutcomeUnknownError):
        capability.attempt_execution(work.pk)
    make_observation_due(work)
    return work
