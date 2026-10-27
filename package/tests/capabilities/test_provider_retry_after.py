"""
A provider's declared wait (HTTP Retry-After) is a floor on the retry, live and recovered.

Design: docs/architecture.md.
"""

from dataclasses import replace
from datetime import timedelta

import pytest
from pydantic import ValidationError

from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState
from due.django.public import WorkFailureDisposition, database_now
from due.django.testing import expire_lease, use_database_clock
from due.errors import EffectNotAppliedError, EffectRejection
from tests.harness.operation import Publication, record
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import PrimitiveEffect, PrimitiveWork, primitive
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.primitive import record as record_primitive

pytestmark = pytest.mark.django_db(transaction=True)

POLICY_DELAY = timedelta(minutes=1)  # DurableWorkPolicy's default retry_delay


@pytest.mark.parametrize(
    "retry_after,expected",
    [
        (timedelta(hours=1), timedelta(hours=1)),
        # Provider waiting is not a failed attempt: use its requested interval.
        (timedelta(seconds=5), timedelta(seconds=5)),
        (None, POLICY_DELAY),
    ],
)
def test_live_rejection_defers_the_retry_to_the_providers_wait(publication, monkeypatch, retry_after, expected):
    work = record(publication)
    decision = EffectRejection(message="rate limited", retry_after=retry_after)
    monkeypatch.setattr(Publication, "failure", EffectNotAppliedError(decision))
    now = database_now()

    with use_database_clock(lambda now=now: now), pytest.raises(EffectNotAppliedError):
        publication.attempt_execution(work.pk)

    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert work.available_at == now + expected
    assert work.rearm_count == (1 if retry_after is None else 0), "provider waiting must not spend failure budget"


def test_recovered_rejection_keeps_the_providers_wait(publication, monkeypatch):
    work = record(publication)
    decision = EffectRejection(message="rate limited", retry_after=timedelta(hours=1))
    monkeypatch.setattr(Publication, "failure", EffectNotAppliedError(decision))

    def fail_settlement(self, current, error):
        raise RuntimeError("local product write failed")

    with monkeypatch.context() as fault:
        fault.setattr(Publication, "finalize_retry", fail_settlement)
        with pytest.raises(RuntimeError, match="local product"):
            publication.attempt_execution(work.pk)
    expire_lease(work)
    settled = database_now()

    with use_database_clock(lambda: settled):
        publication.recover()

    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert work.available_at == settled + timedelta(hours=1), "recovery read the wait back from the evidence"
    assert work.rearm_count == 0, "recovered provider waits preserve the failure budget"
    assert len(Publication.calls) == 1


def test_persisted_decision_omits_an_unset_wait_and_round_trips_a_set_one(publication, monkeypatch):
    unset = EffectRejection(message="rejected")
    assert "retry_after" not in unset.model_dump(mode="json"), "evidence without a wait reads on older versions"
    work = record(publication)
    decision = EffectRejection(message="rate limited", retry_after=timedelta(seconds=90))
    monkeypatch.setattr(Publication, "failure", EffectNotAppliedError(decision))
    with pytest.raises(EffectNotAppliedError):
        publication.attempt_execution(work.pk)
    stored = attempts_for(work).get().rejection
    assert EffectRejection.model_validate(stored) == decision


@pytest.mark.parametrize(
    "fields,message",
    [
        ({"retry_after": timedelta(seconds=-1)}, "nonnegative"),
        ({"retry_after": timedelta(seconds=1), "disposition": WorkFailureDisposition.TERMINAL}, "terminal"),
    ],
)
def test_a_wait_belongs_only_to_a_retry(fields, message):
    with pytest.raises(ValidationError, match=message):
        EffectRejection(message="rejected", **fields)


class _Throttled(OSError):
    def __init__(self, retry_after: timedelta) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


def test_replay_safe_work_reads_the_wait_off_its_own_error(isolated_primitive, monkeypatch):
    capability = replace(primitive, policy=replace(primitive.policy, retry_delay=timedelta(seconds=2)))

    def fail(self: PrimitiveEffect, work: PrimitiveWork) -> str:
        raise _Throttled(timedelta(minutes=10))

    def provider_retry_after(self: PrimitiveEffect, error: Exception) -> timedelta:
        return error.retry_after if isinstance(error, _Throttled) else timedelta(0)

    monkeypatch.setattr(PrimitiveEffect, "perform_effect", fail)
    monkeypatch.setattr(PrimitiveEffect, "provider_retry_after", provider_retry_after)
    work = record_primitive()
    now = database_now()

    with use_database_clock(lambda now=now: now), pytest.raises(_Throttled):
        capability.attempt_execution(work.pk)

    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert work.available_at == now + timedelta(minutes=10)
    assert work.rearm_count == 0


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("hint", [None, timedelta(seconds=17)])
def test_unavailability_waits_beyond_failure_budget_then_completes(isolated_primitive, monkeypatch, status, hint):
    from due.errors import WorkDeferredError

    capability = replace(primitive, policy=replace(primitive.policy, max_rearms=1, retry_delay=timedelta(seconds=3)))
    work = record_primitive()
    now = database_now()
    calls = []

    def unavailable(self, work):
        calls.append(work.pk)
        raise WorkDeferredError(f"HTTP {status}", retry_after=hint)

    with monkeypatch.context() as fault:
        fault.setattr(PrimitiveEffect, "perform_effect", unavailable)
        for execution in range(7):
            with use_database_clock(lambda now=now: now):
                assert capability.attempt_execution(work.pk)
                work.refresh_from_db()
                assert work.rearm_count == 0
                assert work.state == DurableWorkState.RETRYABLE
                assert work.available_at == now + (hint or timedelta(seconds=3))
                assert not capability.attempt_execution(work.pk)
                assert len(calls) == execution + 1
            now = work.available_at
    with use_database_clock(lambda now=now: now):
        assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED


def test_a_503_does_not_authorize_replaying_a_possible_effect(publication, monkeypatch):
    from due.errors import EffectOutcomeUnknownError, WorkDeferredError

    work = record(publication)
    monkeypatch.setattr(Publication, "failure", WorkDeferredError("HTTP 503", retry_after=timedelta(seconds=30)))
    with pytest.raises(EffectOutcomeUnknownError):
        publication.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.UNKNOWN
    assert work.rearm_count == 0
    assert not publication.attempt_execution(work.pk)
    assert len(Publication.calls) == 1
