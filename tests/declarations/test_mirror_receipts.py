"""
Ordinary execution and late evidence repair share the same mirror/hook contract.

Design: docs/architecture.md#application-declarations.
"""

import pytest

from due.django.evidence.attempts import attempts_for, observe_effect_outcome
from due.django.models import EffectAttemptOutcome
from due.django.public import DurableWorkState
from tests.harness.operation import (
    Publication,
    PublicationWork,
    Receipt,
    record,
)
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)


def test_failed_hook_rolls_back_mirror_and_late_receipt_repeats_both(publication, monkeypatch):
    observed = []

    def hook(self, work, result):
        observed.append(PrimitiveOwner.objects.get(pk=work.owner_id).value)
        if len(observed) == 1:
            raise RuntimeError("settlement failed after mirror")

    monkeypatch.setattr(Publication, "finalize_applied", hook)
    work = record(publication)
    with pytest.raises(RuntimeError, match="settlement failed"):
        publication.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.UNKNOWN
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "unknown"
    assert Receipt.objects.get().result == {"value": "applied"}
    assert publication.settle_from_applied_effect_evidence(work.pk)
    assert not publication.settle_from_applied_effect_evidence(work.pk)
    assert observed == ["applied", "applied"]
    assert Publication.calls == ["applied"]
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "applied"
    assert not attempts_for(work).get().repair_pending
    assert work.has_possible_effect()  # Settled evidence still forbids duplicate publication.


def test_legacy_unknown_and_late_negative_evidence_use_mirrors(publication):
    with publication.atomic(PrimitiveOwner.objects.create().pk, notify_worker=False) as pending:
        work = pending.request_work(PublicationWork(payload="never sent", universe="acme"))
        publication.record_unknown_effect_outcome(work)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "unknown"
    observe_effect_outcome(attempts_for(work).get(), EffectAttemptOutcome.NOT_APPLIED)
    assert publication.reconcile_nonapplication(work.pk)
    assert not work.has_possible_effect()
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "retry"
    assert Publication.calls == []


@pytest.mark.parametrize("destination", ["source", "source_id"])
def test_explicit_relation_or_none_overrides_both_capture_spellings(publication, destination):
    from dataclasses import replace

    from due.django.public import Capture

    capability = replace(
        publication, capture=Capture(source="id") if destination == "source" else Capture(source_id="id")
    )
    owner = PrimitiveOwner.objects.create()
    with capability.atomic(owner.pk, notify_worker=False) as pending:
        assert pending.capture_work().source_id == owner.pk
        assert pending.capture_work(source=None).source_id is None
        assert pending.capture_work(source_id=None).source_id is None
        assert pending.capture_work(source=owner).source_id == owner.pk
        with pytest.raises(ValueError, match="one spelling"):
            pending.capture_work(source=owner, source_id=owner.pk)


def test_capture_declaration_rejects_duplicate_relation_destinations(publication):
    from dataclasses import replace

    from due.django.public import Capture

    with pytest.raises(ValueError, match="one spelling"):
        replace(publication, capture=Capture(source="id", source_id="id"))
