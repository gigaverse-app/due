"""
Declared delays preserve counter timing across execution and claim recovery.

Design: docs/architecture.md.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from due.django.public import (
    ContinueAfter,
    DurableWorkState,
    ExponentialDelay,
    WorkExecutionContext,
    WorkFailureDisposition,
    claim_work,
    database_now,
)
from due.django.testing import expire_lease, use_database_clock
from tests.harness.primitive import (
    PrimitiveEffect,
    PrimitiveWork,
    primitive,
    record,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


@pytest.fixture
def capability() -> PrimitiveEffect:
    return replace(
        primitive,
        policy=replace(
            primitive.policy,
            retry_delay=ExponentialDelay(initial=timedelta(minutes=1), maximum=timedelta(minutes=15)),
            max_rearms=8,
        ),
    )


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("step,seconds", [(0, 60), (1, 120), (4, 900)])
@pytest.mark.parametrize("override", [False, True])
def test_execution_and_reaper_use_pre_transition_delay(
    capability: PrimitiveEffect, monkeypatch: pytest.MonkeyPatch, expired: bool, step: int, seconds: int, override: bool
) -> None:
    def fail(self: PrimitiveEffect, work: PrimitiveWork) -> str:
        raise OSError("unavailable")

    monkeypatch.setattr(PrimitiveEffect, "perform_effect", fail)
    if override:
        monkeypatch.setattr(PrimitiveEffect, "retry_delay_for", lambda self, work: timedelta(seconds=37))
    work = record()
    PrimitiveWork.objects.filter(pk=work.pk).update(rearm_count=step)
    # Freeze the library at PostgreSQL's time: record() stamped available_at on that clock,
    # and a host-clock freeze behind it cannot claim the work.
    now = database_now()
    with use_database_clock(lambda: now):
        if expired:
            assert claim_work(PrimitiveWork, work.pk, lease_duration=capability.policy.lease_duration) is not None
            expire_lease(work)
            assert capability.recover() == 0
        else:
            with pytest.raises(OSError, match="unavailable"):
                capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert work.rearm_count == step + 1
    assert work.available_at == now + timedelta(seconds=37 if override else seconds)


def test_exponential_delay_does_not_delay_or_charge_lost_delivery(capability: PrimitiveEffect) -> None:
    work = record()
    capability.enqueue_execution(work.pk)
    old_delivery = PrimitiveEffect.deliveries[-1]
    expire_lease(work)
    assert capability.recover() == 1
    work.refresh_from_db()
    assert work.rearm_count == 0
    assert work.state == DurableWorkState.QUEUED
    assert not capability.attempt_execution(*old_delivery)
    assert capability.attempt_execution(*PrimitiveEffect.deliveries[-1])


@pytest.mark.parametrize("expired", [False, True])
def test_exhaustion_and_operator_rearm_keep_their_budget_rules(
    capability: PrimitiveEffect, monkeypatch: pytest.MonkeyPatch, expired: bool
) -> None:

    def fail(self: PrimitiveEffect, work: PrimitiveWork) -> str:
        raise OSError("unavailable")

    monkeypatch.setattr(PrimitiveEffect, "perform_effect", fail)
    work = record()
    PrimitiveWork.objects.filter(pk=work.pk).update(rearm_count=capability.policy.max_rearms)
    original_available_at = work.available_at
    if expired:
        assert claim_work(PrimitiveWork, work.pk, lease_duration=capability.policy.lease_duration) is not None
        expire_lease(work)
        assert capability.recover() == 0
    else:
        with pytest.raises(OSError):
            capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.EXHAUSTED
    assert work.rearm_count == capability.policy.max_rearms
    assert work.available_at == original_available_at
    assert work.owner_id is not None
    with capability.atomic(work.owner_id, notify_worker=False) as pending:
        assert pending.retry_failed()
    work.refresh_from_db()
    assert work.rearm_count == 0
    assert capability.retry_delay_for(work) == timedelta(minutes=1)


@pytest.mark.parametrize("history", ["unknown", "terminal", "continuation"])
def test_delay_cannot_authorize_retry_or_change_continuation_timing(
    capability: PrimitiveEffect, monkeypatch: pytest.MonkeyPatch, history: str
) -> None:

    def apply(
        self: PrimitiveEffect, work: PrimitiveWork, context: WorkExecutionContext[PrimitiveWork]
    ) -> ContinueAfter:
        if history == "continuation":
            return ContinueAfter(timedelta(seconds=7), until=None)
        raise OSError("unavailable")

    disposition = WorkFailureDisposition.UNKNOWN if history == "unknown" else WorkFailureDisposition.TERMINAL
    monkeypatch.setattr(PrimitiveEffect, "apply", apply)
    monkeypatch.setattr(PrimitiveEffect, "classify_error", lambda self, error: disposition)
    work = record()
    original_available_at = work.available_at
    now = database_now()
    with use_database_clock(lambda: now):
        if history == "continuation":
            assert capability.attempt_execution(work.pk)
        else:
            with pytest.raises(OSError):
                capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.rearm_count == 0
    if history == "continuation":
        assert work.state == DurableWorkState.READY
        assert work.available_at == now + timedelta(seconds=7)
    else:
        expected = DurableWorkState.UNKNOWN if history == "unknown" else DurableWorkState.TERMINAL_FAILED
        assert work.state == expected
        assert work.available_at == original_available_at
