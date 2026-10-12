"""Database transitions and their continuation are one fenced commit."""

from datetime import timedelta

import pytest
from django.db import transaction

from due.django.public import ContinueAfter, DatabaseEffect, DurableWorkState, require_fenced_settlement
from due.django.runtime import coordination
from due.django.testing import make_due
from tests.harness.primitive import PrimitiveOwner, PrimitiveWork, primitive, record
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


class Advance(DatabaseEffect[PrimitiveOwner, PrimitiveWork]):
    def transition(self, work: PrimitiveWork) -> ContinueAfter | None:
        require_fenced_settlement(work)
        owner = PrimitiveOwner.objects.get(pk=work.owner_id)
        owner.value += "+"
        owner.save(update_fields=["value"])
        if len(owner.value) < 8:
            return ContinueAfter(timedelta(seconds=1), until=None)
        return None


advance = Advance.for_owner(
    owner=PrimitiveOwner, work=PrimitiveWork, input_fields=("universe", "payload"), policy=primitive.policy
)


def value(work):
    return PrimitiveOwner.objects.get(pk=work.owner_id).value


def test_duplicate_delivery_cannot_repeat_committed_transition():
    work = record()
    advance.enqueue_execution(work.pk)
    work.refresh_from_db()
    token = work.fencing_token
    assert advance.attempt_execution(work.pk, token)
    assert not advance.attempt_execution(work.pk, token)
    assert value(work) == "before+"
    make_due(work)
    assert advance.attempt_execution(work.pk)
    work.refresh_from_db()
    assert value(work) == "before++"
    assert work.state == DurableWorkState.SUCCEEDED


@pytest.mark.parametrize("boundary", ["continue_work", "complete_work"])
def test_settlement_failure_rolls_back_product_transition(boundary, monkeypatch):
    work = record()
    if boundary == "complete_work":
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="before+")
    initial = value(work)

    def fail(*args, **kwargs):
        raise OSError("commit unavailable")

    with monkeypatch.context() as fault:
        fault.setattr(coordination, boundary, fail)
        with pytest.raises(OSError, match="commit unavailable"):
            advance.attempt_execution(work.pk)
    assert value(work) == initial
    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    make_due(work)
    assert advance.attempt_execution(work.pk)
    assert value(work) == initial + "+"


def test_transition_refuses_unfenced_direct_call():
    work = record()
    with transaction.atomic(), pytest.raises(RuntimeError, match="fenced settlement"):
        advance.transition(work)
    assert value(work) == "before"


def test_superseded_claim_never_enters_database_callback(monkeypatch):
    work = record()
    claim = coordination.claim_work(PrimitiveWork, work.pk, lease_duration=primitive.policy.lease_duration)
    assert claim is not None
    coordination.supersede_work(PrimitiveWork, work.pk)
    monkeypatch.setattr(coordination, "claim_work", lambda *args, **kwargs: claim)
    assert not advance.attempt_execution(work.pk)
    assert value(work) == "before"


def test_guarded_provider_call_is_rejected_inside_database_transition():
    work = record()

    def handler(current, context):
        PrimitiveOwner.objects.filter(pk=current.owner_id).update(value="should roll back")
        with context.external_call():
            pytest.fail("provider entry inside transaction")

    with pytest.raises(RuntimeError, match="autocommit"):
        coordination.run_work(
            PrimitiveWork,
            work.pk,
            handler=handler,
            classify_error=lambda error: coordination.WorkFailureDisposition.RETRYABLE,
            lease_duration=primitive.policy.lease_duration,
            max_rearms=3,
            retry_delay=timedelta(0),
            database_only=True,
        )
    assert value(work) == "before"


def test_rollback_after_continuation_write_discards_both_rows(monkeypatch):
    work = record()
    original = coordination.continue_work

    def fail_after(*args, **kwargs):
        assert original(*args, **kwargs)
        raise OSError("lost transaction")

    monkeypatch.setattr(coordination, "continue_work", fail_after)
    with pytest.raises(OSError, match="lost transaction"):
        advance.attempt_execution(work.pk)
    assert value(work) == "before"
    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert work.applied_revision is None


def test_database_callback_cannot_replace_runtime_guard():
    class Unguarded(Advance):
        def apply(self, work, context):  # pyrefly: ignore [bad-override] - intentional invalid authoring control
            return self.transition(work)

    with pytest.raises(TypeError, match="runtime-owned"):
        Unguarded.for_owner(
            owner=PrimitiveOwner, work=PrimitiveWork, input_fields=("universe", "payload"), policy=primitive.policy
        )


@pytest.mark.parametrize("fail_settlement", [False, True])
def test_database_transition_admits_successor_in_same_commit(monkeypatch, fail_settlement):
    target = PrimitiveOwner.objects.create()

    class Handoff(DatabaseEffect[PrimitiveOwner, PrimitiveWork]):
        def transition(self, work):
            with primitive.atomic(target.pk) as pending:
                pending.request_work(PrimitiveWork(universe="acme", payload="successor"))
            PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="handed off")

    capability = Handoff.for_owner(
        owner=PrimitiveOwner, work=PrimitiveWork, input_fields=("universe", "payload"), policy=primitive.policy
    )
    work = record()
    if fail_settlement:

        def fail(*args, **kwargs):
            raise OSError("settlement failed")

        monkeypatch.setattr(coordination, "complete_work", fail)
        with pytest.raises(OSError, match="settlement failed"):
            capability.attempt_execution(work.pk)
    else:
        assert capability.attempt_execution(work.pk)
    assert PrimitiveWork.objects.filter(owner=target).exists() is not fail_settlement
    assert value(work) == ("before" if fail_settlement else "handed off")
