"""
Delivery loss must not spend the application's execution failure budget.

Design: docs/architecture.md.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from due.django.models import DurableWorkState
from due.django.runtime.coordination import (
    claim_work,
    database_now,
    reap_expired_work,
    terminal_work,
)
from tests.harness.primitive import (
    PrimitiveEffect,
    PrimitiveOwner,
    PrimitiveWork,
    primitive,
    record,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)


@pytest.mark.usefixtures("isolated_primitive")
def test_repeated_lost_deliveries_preserve_budget_and_reject_old_tokens() -> None:
    work = record()
    primitive.enqueue_execution(work.pk)
    assert primitive.policy.max_rearms is not None
    for _ in range(primitive.policy.max_rearms + 2):
        lost_id, lost_token = PrimitiveEffect.deliveries[-1]
        PrimitiveWork.objects.filter(pk=work.pk).update(lease_expires_at=database_now() - timedelta(seconds=1))
        assert primitive.recover() == 1
        work.refresh_from_db()
        assert work.state == DurableWorkState.QUEUED
        assert work.rearm_count == 0
        assert not primitive.attempt_execution(lost_id, lost_token)
    assert PrimitiveEffect.calls == []
    assert primitive.attempt_execution(*PrimitiveEffect.deliveries[-1])
    assert PrimitiveEffect.calls == [work.payload]


@pytest.mark.usefixtures("isolated_primitive")
def test_expired_execution_still_spends_failure_budget() -> None:
    work = record()
    assert primitive.policy.max_rearms is not None
    for _ in range(primitive.policy.max_rearms + 1):
        assert claim_work(PrimitiveWork, work.pk, lease_duration=primitive.policy.lease_duration)
        PrimitiveWork.objects.filter(pk=work.pk).update(lease_expires_at=database_now() - timedelta(seconds=1))
        # Reap without publishing another reservation, so the next iteration
        # represents an execution claim rather than an undelivered message.
        reap_expired_work(PrimitiveWork, max_rearms=primitive.policy.max_rearms, retry_delay=timedelta(0))
    work.refresh_from_db()
    assert work.state == DurableWorkState.EXHAUSTED
    assert not primitive.attempt_execution(work.pk)


@pytest.mark.usefixtures("isolated_primitive")
def test_unbounded_policy_survives_repeated_execution_failures_then_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    capability = replace(primitive, policy=replace(primitive.policy, max_rearms=None))
    work = record()

    def fail(self: PrimitiveEffect, work: PrimitiveWork) -> str:
        raise OSError("provider outage")

    with monkeypatch.context() as outage:
        outage.setattr(PrimitiveEffect, "perform_effect", fail)
        for _ in range(12):
            with pytest.raises(OSError, match="provider outage"):
                capability.attempt_execution(work.pk)
            work.refresh_from_db()
            assert work.state == DurableWorkState.RETRYABLE
    assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED


@pytest.mark.usefixtures("isolated_primitive")
def test_explicit_retry_reopens_failed_input_but_duplicate_requirement_does_not() -> None:
    work = record()
    claim = claim_work(PrimitiveWork, work.pk, lease_duration=primitive.policy.lease_duration)
    assert claim is not None
    assert terminal_work(claim, error_code="configuration_invalid")
    assert work.owner_id is not None
    with primitive.atomic(work.owner_id, notify_worker=False) as pending:
        unchanged = pending.request_work(PrimitiveWork(payload=work.payload, universe=work.universe))
        assert unchanged.state == DurableWorkState.TERMINAL_FAILED
    assert work.owner_id is not None
    with primitive.atomic(work.owner_id, notify_worker=False) as pending:
        assert pending.retry_failed()
    work.refresh_from_db()
    assert work.desired_revision == claim.desired_revision + 1
    assert not terminal_work(claim, error_code="stale_failure")
    assert primitive.attempt_execution(work.pk)
    assert work.owner_id is not None
    with primitive.atomic(work.owner_id, notify_worker=False) as pending:
        assert not pending.retry_failed()
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == work.payload


@pytest.mark.usefixtures("isolated_primitive")
def test_recovery_publishes_exhaustion_to_the_product_after_worker_death() -> None:
    capability = replace(primitive, policy=replace(primitive.policy, max_rearms=0))
    work = record()
    assert claim_work(PrimitiveWork, work.pk, lease_duration=capability.policy.lease_duration)
    PrimitiveWork.objects.filter(pk=work.pk).update(lease_expires_at=database_now() - timedelta(seconds=1))
    capability.recover()
    work.refresh_from_db()
    assert work.state == DurableWorkState.EXHAUSTED
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "exhausted"
