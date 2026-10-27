"""Renewal uses one database statement without extending leases through lock waits."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, transaction
from django.test.utils import CaptureQueriesContext

from due.django.runtime.coordination import claim_work, database_now, renew_work
from due.django.testing import use_database_clock
from tests.harness.primitive import PrimitiveWork, record
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]
LEASE = timedelta(minutes=2)


@pytest.fixture
def claim():
    work = record()
    claimed = claim_work(PrimitiveWork, work.pk, lease_duration=LEASE)
    assert claimed is not None
    return claimed


@pytest.mark.parametrize("owned", [True, False])
def test_renewal_needs_one_statement_and_preserves_ownership(claim, owned):
    original_expiry = claim.work.lease_expires_at
    before = database_now()
    with CaptureQueriesContext(connection) as queries:
        renewed = renew_work(claim if owned else replace(claim, token=uuid4()), lease_duration=LEASE)
    after = database_now()
    claim.work.refresh_from_db()
    assert renewed is owned
    if owned:
        assert before <= claim.work.updated_at <= after
        assert claim.work.lease_expires_at == claim.work.updated_at + LEASE
    else:
        assert claim.work.lease_expires_at == original_expiry
    assert len(queries) == 1


@pytest.mark.parametrize("explicit", [True, False])
def test_renewal_respects_custom_clock_and_restores_real_clock(claim, explicit):
    controlled = database_now() - timedelta(days=1)
    calls = []

    def now():
        calls.append(controlled)
        return controlled

    with pytest.raises(RuntimeError, match="exit clock"), use_database_clock(now):
        assert renew_work(claim, lease_duration=LEASE, **({"now": now} if explicit else {}))
        claim.work.refresh_from_db()
        assert claim.work.updated_at == controlled
        assert claim.work.lease_expires_at == controlled + LEASE
        assert calls == [controlled]
        raise RuntimeError("exit clock")
    assert renew_work(claim, lease_duration=LEASE)
    claim.work.refresh_from_db()
    assert claim.work.updated_at > controlled
    assert claim.work.lease_expires_at == claim.work.updated_at + LEASE


def test_lock_wait_consumes_renewed_lease(claim):
    ready = Event()
    backend_pid = []

    def renew_on_another_connection():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                backend_pid.append(cursor.fetchone()[0])
            ready.set()
            return renew_work(claim, lease_duration=LEASE)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            PrimitiveWork.objects.select_for_update().get(pk=claim.work_id)
            pending = pool.submit(renew_on_another_connection)
            assert ready.wait(5), "renewal connection never opened"
            deadline = monotonic() + 5
            with connection.cursor() as cursor:
                while True:
                    # Clear this transaction's statistics snapshot on each poll.
                    cursor.execute("SELECT pg_stat_clear_snapshot()")
                    cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", backend_pid)
                    if cursor.fetchone()[0] == "Lock":
                        break
                    assert monotonic() < deadline, "renewal did not wait for the real row lock"
                    Event().wait(0.01)
            releasing_at = database_now()
        assert pending.result(timeout=5)
    claim.work.refresh_from_db()
    assert claim.work.updated_at < releasing_at
    assert claim.work.lease_expires_at == claim.work.updated_at + LEASE
