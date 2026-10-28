"""Claims obtain their database timestamp with the existing locked selection."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
from time import monotonic

import pytest
from django.db import close_old_connections, connection, transaction
from django.db.models import Q
from django.test.utils import CaptureQueriesContext

from due.django.declarations.eligibility import ExecutionEligibility
from due.django.models import DurableWorkState
from due.django.runtime.coordination import claim_work, database_now
from due.django.testing import use_database_clock
from tests.harness.primitive import PrimitiveWork, record
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]
LEASE = timedelta(minutes=2)


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("eligible", [False, True])
def test_static_eligibility_uses_the_claim_statement_clock(queued, eligible):
    work = record()
    condition = Q(pk=work.pk if eligible else -1)
    gate = ExecutionEligibility(condition=condition)
    before = database_now()
    with CaptureQueriesContext(connection) as queries:
        claim = claim_work(PrimitiveWork, work.pk, lease_duration=LEASE, queued=queued, eligibility=gate)
    after = database_now()
    assert (claim is not None) is eligible
    if claim is not None:
        assert before <= claim.work.lease_expires_at - LEASE <= after
    assert len([q for q in queries if q["sql"].startswith("SELECT")]) == 1
    assert gate.condition == condition


def test_static_fallback_does_not_accumulate_old_deadlines_or_bypass_backoff():
    work = record()
    condition = Q(pk=-1)
    gate = ExecutionEligibility(condition=condition, recheck_after=timedelta(minutes=5))
    now = database_now()
    PrimitiveWork.objects.filter(pk=work.pk).update(available_at=now - timedelta(minutes=6))
    assert gate.filter(PrimitiveWork.objects.all(), now=now).exists()
    assert not gate.filter(PrimitiveWork.objects.all(), now=now - timedelta(minutes=2)).exists()
    assert gate.condition == condition == Q(pk=-1)
    PrimitiveWork.objects.filter(pk=work.pk).update(available_at=now + timedelta(minutes=1))
    assert claim_work(PrimitiveWork, work.pk, lease_duration=LEASE, eligibility=gate, now=lambda: now) is None


def test_static_eligibility_preserves_explicit_clock():
    work = record()
    now = database_now() + timedelta(days=1)
    calls = []

    def clock():
        calls.append(now)
        return now

    claim = claim_work(
        PrimitiveWork, work.pk, lease_duration=LEASE, now=clock, eligibility=ExecutionEligibility(Q(pk=work.pk))
    )
    assert claim is not None
    assert claim.work.lease_expires_at == now + LEASE
    assert calls == [now]


@pytest.mark.parametrize("queued", [False, True])
def test_claim_returns_database_time_without_an_extra_query(queued):
    work = record()
    before = database_now()
    with CaptureQueriesContext(connection) as queries:
        claim = claim_work(PrimitiveWork, work.pk, lease_duration=LEASE, queued=queued)
    after = database_now()
    assert claim is not None
    work.refresh_from_db()
    assert work.state == (DurableWorkState.QUEUED if queued else DurableWorkState.CLAIMED)
    assert work.lease_expires_at == claim.work.lease_expires_at
    assert work.lease_expires_at is not None
    assert before <= work.lease_expires_at - LEASE <= after
    assert work.attempt_count == work.claim_generation == 1
    assert work.fencing_token == claim.token
    statements = [q["sql"] for q in queries if q["sql"].startswith(("SELECT", "UPDATE"))]
    assert len(statements) == 2, statements


@pytest.mark.parametrize("reason", ["future", "revision", "claimed"])
def test_missed_claim_needs_only_its_locked_selection(reason):
    work = record()
    if reason == "future":
        PrimitiveWork.objects.filter(pk=work.pk).update(available_at=database_now() + timedelta(days=1))
    elif reason == "claimed":
        assert claim_work(PrimitiveWork, work.pk, lease_duration=LEASE) is not None
    work.refresh_from_db()
    prior = (work.state, work.fencing_token, work.attempt_count, work.lease_expires_at)
    with CaptureQueriesContext(connection) as queries:
        assert (
            claim_work(
                PrimitiveWork,
                work.pk,
                lease_duration=LEASE,
                expected_revision=work.desired_revision + 1 if reason == "revision" else None,
            )
            is None
        )
    work.refresh_from_db()
    assert (work.state, work.fencing_token, work.attempt_count, work.lease_expires_at) == prior
    assert len([q for q in queries if q["sql"].startswith("SELECT")]) == 1


@pytest.mark.parametrize("explicit", [False, True])
def test_claim_clock_control_is_sampled_once_and_uses_its_concrete_value(explicit):
    work = record()
    controlled = database_now() + timedelta(days=1)
    calls = []

    def clock():
        calls.append(controlled)
        return controlled

    with use_database_clock(clock):
        claim = claim_work(PrimitiveWork, work.pk, lease_duration=LEASE, now=clock if explicit else database_now)
    assert claim is not None
    assert claim.work.lease_expires_at == controlled + LEASE
    assert calls == [controlled]


@pytest.mark.parametrize("eligible", [False, True])
def test_eligibility_callback_still_receives_a_concrete_clock_value(eligible):
    work = record()
    controlled = database_now() + timedelta(days=1)
    observed = []

    def condition(now):
        assert isinstance(now, datetime)
        observed.append(now)
        return Q(pk=work.pk if eligible else -1)

    with use_database_clock(lambda: controlled):
        claim = claim_work(
            PrimitiveWork,
            work.pk,
            lease_duration=LEASE,
            eligibility=ExecutionEligibility(condition=condition),
        )
    assert observed == [controlled]
    assert (claim is not None) is eligible
    if claim is not None:
        assert claim.work.lease_expires_at == controlled + LEASE


def test_lock_wait_consumes_claim_lease():
    work = record()
    ready = Event()
    backend_pid = []

    def claim_on_another_connection():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                backend_pid.append(cursor.fetchone()[0])
            ready.set()
            return claim_work(PrimitiveWork, work.pk, lease_duration=LEASE)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            PrimitiveWork.objects.select_for_update().get(pk=work.pk)
            pending = pool.submit(claim_on_another_connection)
            assert ready.wait(5), "claim connection never opened"
            deadline = monotonic() + 5
            with connection.cursor() as cursor:
                while True:
                    cursor.execute("SELECT pg_stat_clear_snapshot()")
                    cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", backend_pid)
                    if cursor.fetchone()[0] == "Lock":
                        break
                    assert monotonic() < deadline, "claim did not wait for the real row lock"
                    Event().wait(0.01)
            releasing_at = database_now()
        claim = pending.result(timeout=5)
    assert claim is not None
    assert claim.work.lease_expires_at is not None
    assert claim.work.lease_expires_at - LEASE < releasing_at
