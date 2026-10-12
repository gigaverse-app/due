"""Test-owned provider operation and real SQL tables for receipt/rejection proofs."""

from collections.abc import Iterator
from typing import ClassVar

import pytest
from django.db import connection, models
from django.test.utils import isolate_apps
from pydantic import BaseModel

from due.django.capabilities.operations import DurableExternalOperation, ExternalOperationWork
from due.django.conformance.suite import DeliveryExample
from due.django.evidence.effect_evidence import (
    JsonAppliedEffectEvidenceStore,
    StoredAppliedEffectEvidence,
)
from due.django.public import DurableWorkPolicy, Outcome, StatusMirror
from due.errors import EffectNotAppliedError
from due.observations import AppliedEffectEvidence
from tests.harness.primitive import PrimitiveOwner

with isolate_apps("tests"):

    class PublicationWork(ExternalOperationWork):
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(PrimitiveOwner, on_delete=models.SET_NULL, null=True)
        payload = models.CharField(max_length=40)
        source = models.ForeignKey(PrimitiveOwner, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

        class Meta:
            app_label = "tests"

    class Receipt(StoredAppliedEffectEvidence):
        class Meta:
            app_label = "tests"


class ProviderResult(BaseModel):
    """Canonical result of this test provider's independent wire contract."""

    value: str


mirror = StatusMirror(lambda work: PrimitiveOwner.objects.filter(pk=work.owner_id))


class Publication(DurableExternalOperation[PrimitiveOwner, PublicationWork, ProviderResult]):
    evidence_store = JsonAppliedEffectEvidenceStore(Receipt, ProviderResult)
    calls: ClassVar[list[str]] = []
    explicit_evidence: ClassVar[bool] = True
    failure: ClassVar[EffectNotAppliedError | None] = None
    on_applied: Outcome[PublicationWork, ProviderResult] = mirror.outcome(lambda work, result: {"value": result.value})
    on_terminal: Outcome[PublicationWork, Exception] = mirror.outcome({"value": "terminal"})
    on_unknown: Outcome[PublicationWork, None] = mirror.outcome({"value": "unknown"})
    on_retry: Outcome[PublicationWork, Exception] = mirror.outcome({"value": "retry"})

    def perform_effect(self, work: PublicationWork) -> ProviderResult | AppliedEffectEvidence[ProviderResult]:
        self.calls.append(work.payload)
        if self.failure is not None:
            raise self.failure
        result = ProviderResult(value=work.payload)
        return AppliedEffectEvidence(result=result) if self.explicit_evidence else result


@pytest.fixture(name="publication", params=[False, True], ids=["canonical-result", "applied-evidence"])
def operation_environment(
    isolated_primitive: None, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Publication]:
    monkeypatch.setattr(Publication, "explicit_evidence", request.param)
    Publication.calls.clear()
    monkeypatch.setattr(Publication, "failure", None)
    with connection.schema_editor() as editor:
        editor.create_model(PublicationWork)
        editor.create_model(Receipt)
    try:
        yield Publication.for_owner(
            owner=PrimitiveOwner, work=PublicationWork, policy=DurableWorkPolicy(retention=None)
        )
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(Receipt)
            editor.delete_model(PublicationWork)


def record(capability: Publication) -> PublicationWork:
    with capability.atomic(PrimitiveOwner.objects.create().pk, notify_worker=False) as pending:
        return pending.request_work(PublicationWork(payload="applied", universe="acme"))


def delivery_example(work: PublicationWork, expected: str) -> DeliveryExample[PublicationWork, str]:
    """Shared proofs observe the owner's mirrored value and, independently, provider calls."""
    return DeliveryExample(
        work=work,
        observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
        expected=expected,
        effect_calls=lambda: len(Publication.calls),
    )
