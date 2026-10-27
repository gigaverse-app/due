"""Provider entry uses one authoritative clock sample after its work lock."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from django.db import close_old_connections, connection, transaction
from django.test.utils import CaptureQueriesContext

from due.django.evidence.attempts import attempts_for, record_effect_attempt
from due.django.runtime.coordination import WorkOwnershipLost, claim_work
from due.django.testing import use_database_clock
from tests.harness.primitive import PrimitiveWork, record
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


def test_attempt_uses_one_database_timestamp_for_lease_and_start() -> None:
    work = record()
    claim = claim_work(PrimitiveWork, work.pk, lease_duration=timedelta(minutes=5))
    assert claim is not None
    with CaptureQueriesContext(connection) as queries:
        attempt = record_effect_attempt(claim)
    reads = [q for q in queries if q["sql"] == "SELECT clock_timestamp()"]
    assert len(reads) == 1
    assert claim.work.lease_expires_at is not None
    assert attempt.started_at < claim.work.lease_expires_at


def test_attempt_cannot_use_a_lease_that_expires_while_waiting_for_the_lock() -> None:
    work = record()
    claim = claim_work(PrimitiveWork, work.pk, lease_duration=timedelta(minutes=5))
    assert claim is not None and claim.work.lease_expires_at is not None
    deadline = claim.work.lease_expires_at
    clock = [deadline - timedelta(seconds=1)]
    held, waiting = Event(), Event()

    def hold_until_expired() -> None:
        close_old_connections()
        try:
            with transaction.atomic():
                PrimitiveWork.objects.select_for_update().get(pk=work.pk)
                held.set()
                assert waiting.wait(5), "provider entry never reached the lock"
                clock[0] = deadline + timedelta(seconds=1)
        finally:
            close_old_connections()

    def observe_lock(execute, sql, params, many, context):
        if "FOR UPDATE" in sql and PrimitiveWork._meta.db_table in sql:
            waiting.set()
        return execute(sql, params, many, context)

    with use_database_clock(lambda: clock[0]), ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(hold_until_expired)
        try:
            assert held.wait(5), "competing transaction never locked the work"
            with connection.execute_wrapper(observe_lock), pytest.raises(WorkOwnershipLost):
                record_effect_attempt(claim)
        finally:
            waiting.set()
            writer.result(timeout=5)
    assert not attempts_for(work).exists()
