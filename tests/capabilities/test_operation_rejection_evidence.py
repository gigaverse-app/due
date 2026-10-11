"""Exact rejection evidence survives crashes independently of product settlement."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from pydantic import ValidationError

from due.django.capabilities import operations
from due.django.evidence.attempts import attempts_for, observe_effect_outcome
from due.django.models import DurableWorkState, EffectAttempt, EffectAttemptOutcome
from due.django.runtime import coordination
from due.django.runtime.coordination import WorkFailureDisposition, claim_work
from due.django.testing import WorkerDied, die_after, expire_lease, make_due
from due.errors import EffectNotAppliedError, EffectRejection
from tests.harness.operation import (
    ProviderResult,
    Publication,
    Receipt,
    record,
)
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_rejection_is_atomic_with_evidence(publication, monkeypatch):
    work = record(publication)
    monkeypatch.setattr(Publication, "failure", EffectNotAppliedError("definitely rejected"))
    # Fault inside the evidence transaction: neither half may commit alone.
    with monkeypatch.context() as fault:
        fault.setattr(operations, "observe_effect_outcome", die_after(operations.observe_effect_outcome))
        with pytest.raises(WorkerDied):
            publication.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    assert attempt.outcome == EffectAttemptOutcome.UNKNOWN
    assert attempt.rejection is None
    expire_lease(work)
    publication.recover()
    work.refresh_from_db()
    assert work.state == DurableWorkState.UNKNOWN
    assert len(Publication.calls) == 1


def test_identical_rejection_is_idempotent_and_conflicts_are_refused(publication, monkeypatch):
    work = record(publication)
    decision = EffectRejection(message="rejected", disposition=WorkFailureDisposition.TERMINAL)
    monkeypatch.setattr(Publication, "failure", EffectNotAppliedError(decision))
    with pytest.raises(EffectNotAppliedError):
        publication.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    assert publication.record_rejection(attempt, decision) == decision
    with pytest.raises(ValueError, match="contradicts"):
        publication.record_rejection(attempt, decision.model_copy(update={"message": "different"}))
    with pytest.raises(ValueError, match="contradicts"):
        publication.record_applied_effect_evidence(attempt, ProviderResult(value="applied"))
    work.refresh_from_db()
    assert work.state == DurableWorkState.TERMINAL_FAILED
    assert attempts_for(work).get().rejection == decision.model_dump(mode="json")


@pytest.mark.parametrize("writer", ["rejection", "receipt"])
@pytest.mark.parametrize(
    "altered", ["pk", "work_model", "persisted_work_model", "work_id", "desired_revision", "claim_token"]
)
def test_evidence_checks_the_complete_persisted_identity(publication, monkeypatch, altered: str, writer: str):
    work = record(publication)
    monkeypatch.setattr(Publication, "failure", EffectNotAppliedError("rejected"))
    with pytest.raises(EffectNotAppliedError):
        publication.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    persisted_id = attempt.pk
    if altered == "pk":
        attempt.pk = uuid4()
    elif altered == "work_model":
        attempt.work_model = "tests.unrelated"
    elif altered == "persisted_work_model":
        EffectAttempt.objects.filter(pk=persisted_id).update(work_model="tests.unrelated")
    elif altered == "work_id":
        attempt.work_id += 1
    elif altered == "desired_revision":
        attempt.desired_revision += 1
    else:
        attempt.claim_token = uuid4()
    message = (
        None
        if altered == "pk"
        else "another operation model"
        if altered == "work_model"
        else "persisted evidence identity"
    )
    with pytest.raises((ValueError, EffectAttempt.DoesNotExist), match=message):
        if writer == "rejection":
            publication.record_rejection(attempt, EffectRejection(message="rejected"))
        else:
            publication.record_applied_effect_evidence(attempt, ProviderResult(value="applied"))
    assert EffectAttempt.objects.get(pk=persisted_id).outcome == EffectAttemptOutcome.NOT_APPLIED
    assert not Receipt.objects.exists()
    assert len(Publication.calls) == 1


def test_applied_receipt_cannot_be_replaced_with_rejection(publication):
    work = record(publication)
    publication.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    with pytest.raises(ValueError, match="contradicts"):
        publication.record_rejection(attempt, EffectRejection(message="rejected"))
    assert attempts_for(work).get().outcome == EffectAttemptOutcome.APPLIED


@pytest.mark.parametrize("disposition", [WorkFailureDisposition.RETRYABLE, WorkFailureDisposition.TERMINAL])
def test_failed_settlement_recovers_the_same_rejection(publication, monkeypatch, disposition):
    work = record(publication)
    decision = EffectRejection(message="rejected", disposition=disposition)
    monkeypatch.setattr(Publication, "failure", EffectNotAppliedError(decision))

    def fail_settlement(self, current, error):
        assert error.rejection == decision
        PrimitiveOwner.objects.filter(pk=current.owner_id).update(value="must roll back")
        raise RuntimeError("local product write failed")

    method = "finalize_terminal" if disposition == WorkFailureDisposition.TERMINAL else "finalize_retry"
    with monkeypatch.context() as fault:
        fault.setattr(Publication, method, fail_settlement)
        with pytest.raises(RuntimeError, match="local product"):
            publication.attempt_execution(work.pk)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"
    assert attempts_for(work).get().rejection == decision.model_dump(mode="json")
    expire_lease(work)
    publication.recover()
    work.refresh_from_db()
    expected = (
        DurableWorkState.TERMINAL_FAILED
        if disposition == WorkFailureDisposition.TERMINAL
        else DurableWorkState.RETRYABLE
    )
    assert work.state == expected
    assert work.rearm_count == (0 if disposition == WorkFailureDisposition.TERMINAL else 1)
    publication.recover()
    assert len(Publication.calls) == 1


def test_a_new_claim_cannot_borrow_an_old_rejection(publication, monkeypatch):
    work = record(publication)
    monkeypatch.setattr(Publication, "failure", EffectNotAppliedError("old rejection"))
    with pytest.raises(EffectNotAppliedError):
        publication.attempt_execution(work.pk)
    old_attempt = attempts_for(work).get()
    make_due(work)
    with monkeypatch.context() as fault:
        fault.setattr(coordination, "claim_work", die_after(coordination.claim_work))
        with pytest.raises(WorkerDied):
            publication.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.fencing_token != old_attempt.claim_token
    expire_lease(work)
    publication.recover()
    work.refresh_from_db()
    assert work.last_error_code == "lease_expired"
    assert work.rearm_count == 2
    assert len(Publication.calls) == 1


def test_malformed_stored_decision_blocks_recovery(publication, monkeypatch):
    work = record(publication)
    monkeypatch.setattr(Publication, "failure", EffectNotAppliedError("rejected"))
    with monkeypatch.context() as fault:
        fault.setattr(Publication, "record_rejection", die_after(Publication.record_rejection))
        with pytest.raises(WorkerDied):
            publication.attempt_execution(work.pk)
    attempts_for(work).update(rejection={"version": 999, "message": "unsupported"})
    expire_lease(work)
    with pytest.raises(ExceptionGroup) as raised:
        publication.recover()
    assert raised.group_contains(ValidationError)
    work.refresh_from_db()
    assert work.state == DurableWorkState.CLAIMED
    assert work.rearm_count == 0
    assert len(Publication.calls) == 1


def test_legacy_untyped_nonapplication_keeps_generic_retry(publication):
    work = record(publication)
    with publication.atomic(work.owner_id, notify_worker=False) as pending:
        assert pending.existing_work is not None
        publication.record_unknown_effect_outcome(pending.existing_work)
    attempt = attempts_for(work).get()
    observe_effect_outcome(attempt, EffectAttemptOutcome.NOT_APPLIED)
    assert attempt.rejection is None
    publication.recover()
    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert work.rearm_count == 1
    assert Publication.calls == []


def test_unknown_decision_is_not_definite_rejection():
    with pytest.raises(ValidationError):
        EffectRejection.model_validate({"message": "timeout", "disposition": "unknown"})


def test_database_refuses_rejection_payload_on_unknown_attempt(publication):
    work = record(publication)
    claim = claim_work(type(work), work.pk, lease_duration=publication.policy.lease_duration)
    assert claim is not None
    attempt = operations.record_effect_attempt(claim)
    with pytest.raises(IntegrityError), transaction.atomic():
        EffectAttempt.objects.filter(pk=attempt.pk).update(rejection={"message": "not evidence"})
