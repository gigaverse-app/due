"""
Fault injection must leave actual production transitions observable.

Design and usage: docs/architecture.md.
"""

import pytest
from django.db import transaction

from due.django.public import DurableWorkState
from due.django.testing import expire_lease, hold_nudges, make_due
from tests.harness.primitive import (
    PrimitiveEffect,
    primitive,
    record,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


def test_held_commit_loses_only_fast_wakeup_and_real_recovery_delivers():
    with hold_nudges() as held:
        with transaction.atomic():
            work = record(notify_worker=True)
            assert held == []
        assert [nudge.work_id for nudge in held] == [work.pk]
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY
    assert PrimitiveEffect.deliveries == []
    assert primitive.recover() == 1
    work_id, token = PrimitiveEffect.deliveries.pop()
    assert primitive.attempt_execution(work_id, token)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED


def test_rollback_has_no_nudge_and_scope_exit_restores_fast_delivery():
    with hold_nudges() as held:
        with pytest.raises(RuntimeError), transaction.atomic():
            record(notify_worker=True)
            raise RuntimeError("rollback")
        assert held == []
    work = record(notify_worker=True)
    assert PrimitiveEffect.deliveries[0][0] == work.pk


def test_expiry_changes_only_time_and_recovery_rejects_the_old_delivery():
    work = record()
    with pytest.raises(AssertionError, match="lease to expire"):
        expire_lease(work)
    primitive.enqueue_execution(work.pk)
    _, old_token = PrimitiveEffect.deliveries.pop()
    work.refresh_from_db()
    original = (work.state, work.fencing_token, work.rearm_count, work.desired_revision)
    expire_lease(work)
    make_due(work)
    work.refresh_from_db()
    assert (work.state, work.fencing_token, work.rearm_count, work.desired_revision) == original
    assert primitive.recover() == 1
    assert not primitive.attempt_execution(work.pk, old_token)
    work.refresh_from_db()
    assert work.rearm_count == 0


def test_clock_control_reaches_bound_coordinator_defaults_and_restores_after_failure():
    from datetime import timedelta

    from due.django.runtime.coordination import claim_work, database_now
    from due.django.testing import use_database_clock

    controlled_now = database_now() - timedelta(days=1)
    with pytest.raises(RuntimeError, match="test exits"), use_database_clock(lambda: controlled_now):
        work = record()
        # claim_work bound database_now at import time. Patching an imported
        # alias would leave this default on a different clock from the producer.
        claim = claim_work(type(work), work.pk, lease_duration=timedelta(minutes=2))
        assert claim is not None
        assert claim.work.lease_expires_at == controlled_now + timedelta(minutes=2)
        assert PrimitiveEffect.calls == []
        raise RuntimeError("test exits")
    assert database_now() > controlled_now


def test_death_after_apply_keeps_real_lease_and_recovery_replays_same_input(monkeypatch):
    from due.django.testing import WorkerDied, die_after
    from tests.harness import primitive as fixture

    work = record()
    monkeypatch.setattr(fixture, "call", die_after(fixture.call))
    with pytest.raises(WorkerDied):
        primitive.attempt_execution(work.pk)
    work.refresh_from_db()
    assert PrimitiveEffect.calls == [work.payload]
    assert work.state == DurableWorkState.CLAIMED
    assert work.applied_revision is None
    expire_lease(work)
    primitive.recover()
    _, token = PrimitiveEffect.deliveries[-1]
    assert primitive.attempt_execution(work.pk, token)
    assert PrimitiveEffect.calls == [work.payload, work.payload]
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED


def test_provider_rejection_does_not_consume_the_death_fault():
    from due.django.testing import WorkerDied, die_after

    applied = []

    def provider(*, reject: bool) -> int:
        if reject:
            raise OSError("definite rejection")
        applied.append("accepted")
        return len(applied)

    controlled = die_after(provider)
    with pytest.raises(OSError, match="definite rejection"):
        controlled(reject=True)
    assert applied == []
    with pytest.raises(WorkerDied):
        controlled(reject=False)
    assert applied == ["accepted"]
    assert controlled(reject=False) == 2
