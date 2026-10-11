"""
Audit counterexamples at the committed-claim and provider boundaries.

Design and proof boundaries: docs/architecture.md.
"""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import timedelta
from threading import Event
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection, transaction

from due.django.integration import current_tenant, current_universe_scope
from due.django.public import (
    DurableWorkState,
    WorkExecutionContext,
    WorkFailureDisposition,
    WorkOwnershipLost,
    claim_work,
    complete_work,
    reap_expired_work,
    require_fenced_settlement,
    retry_work,
    run_work,
    supersede_work,
)
from due.django.runtime.counters import CLAIM_COUNTER_CEILING
from due.django.runtime.tasks import run_durable_work_task
from due.django.testing import expire_lease
from tests.harness.primitive import (
    PrimitiveWork,
    primitive,
    record,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

POLICY = primitive.policy

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


def test_execute_refuses_ambient_transaction_before_claim_or_io():
    work = record()
    with patch("tests.harness.primitive.call", return_value="after") as provider:
        with transaction.atomic(), pytest.raises(RuntimeError, match="autocommit"):
            primitive.attempt_execution(work.pk)
        provider.assert_not_called()
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY
    assert work.attempt_count == 0


def test_provider_boundary_refuses_a_handler_opening_its_own_transaction():
    work = record()

    claim = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
    assert claim is not None
    context = WorkExecutionContext(claim, POLICY.lease_duration)
    calls = []
    with transaction.atomic(), pytest.raises(RuntimeError, match="autocommit"), context.external_call():
        calls.append("provider")
    assert calls == []
    with context.external_call():
        calls.append("positive-control")
    assert calls == ["positive-control"]


@pytest.mark.parametrize("fails", [False, True])
def test_universe_covers_handler_classifier_finalizers_and_restores_caller(fails):
    work = record()
    PrimitiveWork.objects.filter(pk=work.pk).update(universe="beta")
    seen = []

    def handler(current, context):
        seen.append(("handler", current_tenant()))
        with context.external_call():
            seen.append(("provider", current_tenant()))
        if fails:
            raise ValueError("permanent failure")

    def classify(error):
        seen.append(("classifier", current_tenant()))
        return WorkFailureDisposition.TERMINAL

    def finalize(current, result):
        seen.append(("finalize", current_tenant()))

    with (
        current_universe_scope("acme"),
        patch.object(PrimitiveWork, "work_beneficiary_user_id", return_value=123),
        patch(
            "due.django.runtime.coordination.pin_after_write",
            side_effect=lambda user_id: seen.append(("pin", current_tenant())),
        ),
    ):
        try:
            run_work(
                PrimitiveWork,
                work.pk,
                handler=handler,
                classify_error=classify,
                lease_duration=POLICY.lease_duration,
                max_rearms=3,
                retry_delay=timedelta(0),
                finalize_success=finalize,
                finalize_terminal=finalize,
            )
        except ValueError:
            assert fails
        assert current_tenant() == "acme"
    assert seen == [
        (stage, "beta")
        for stage in (
            ("handler", "provider", "classifier", "finalize", "pin")
            if fails
            else ("handler", "provider", "finalize", "pin")
        )
    ]


def test_superseded_claim_cannot_enter_provider_or_finalize():
    work = record()

    claim = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
    assert claim is not None
    supersede_work(PrimitiveWork, work.pk)
    calls = []
    with pytest.raises(WorkOwnershipLost), WorkExecutionContext(claim, POLICY.lease_duration).external_call():
        calls.append("provider")
    assert not complete_work(claim, finalize=lambda current: calls.append("finalize"))
    assert calls == []
    successor = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
    assert successor is not None
    assert complete_work(successor, finalize=lambda current: calls.append("finalize"))
    assert calls == ["finalize"]


def test_cleanup_survives_owner_deletion_even_after_claim_snapshot_was_captured():
    work = record()

    claim = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
    assert claim is not None
    room = work.owner
    assert room is not None
    room.delete()
    work.refresh_from_db()
    assert work.owner_id is None
    assert complete_work(claim)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED


def test_duplicate_delivery_has_one_executor_across_real_connections():
    work = record()
    messages = []
    entered, release = Event(), Event()

    def publish(*, args, **options):
        messages.append(args)

    def provider(current):
        assert not connection.in_atomic_block
        entered.set()
        assert release.wait(10)
        return current.payload

    def deliver():
        close_old_connections()
        try:
            return run_durable_work_task(*messages[0])
        finally:
            close_old_connections()

    with patch("due.django.runtime.tasks.run_durable_work_task.apply_async", publish):
        assert primitive.recover() == 1
    with patch("tests.harness.primitive.call", provider):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(deliver)
            try:
                assert entered.wait(10)
                assert pool.submit(deliver).result(timeout=10) is False
            finally:
                release.set()
            assert first.result(timeout=10) is True
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.attempt_count == 1


def test_ambiguous_publication_keeps_reservation_and_old_message_is_fenced_after_reap():
    work = record()
    messages = []

    def send_then_lose_ack(*, args, **options):
        messages.append(args)
        raise ConnectionError("broker accepted, response lost")

    with patch("due.django.runtime.tasks.run_durable_work_task.apply_async", send_then_lose_ack):
        with pytest.raises(ExceptionGroup) as raised:
            primitive.recover()  # The ambiguous publication fails this tick loudly...
        assert raised.group_contains(ConnectionError)
        assert primitive.recover() == 0  # ...and keeps its reservation, so the next tick sends nothing.
    assert len(messages) == 1
    expire_lease(work)
    assert reap_expired_work(PrimitiveWork, max_rearms=3, retry_delay=timedelta(0)) == 1
    with patch("tests.harness.primitive.call", return_value="after") as provider:
        assert run_durable_work_task(*messages[0]) is False
        provider.assert_not_called()
        assert primitive.attempt_execution(work.pk) is True
        provider.assert_called_once()


def test_settlement_permission_is_specific_and_expires_even_in_a_copied_context():
    work, other = record(), record()
    claim = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
    assert claim is not None
    saved = []

    def finalize(current):
        require_fenced_settlement(current)
        with pytest.raises(RuntimeError, match="fenced settlement"):
            require_fenced_settlement(other)
        saved.append(copy_context())

    assert complete_work(claim, finalize=finalize)
    with transaction.atomic(), pytest.raises(RuntimeError, match="fenced settlement"):
        saved[0].run(require_fenced_settlement, claim.work)


def test_synchronous_execution_consumes_a_reservation_and_late_delivery_is_harmless():
    work = record()
    queued = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration, queued=True)
    assert queued is not None
    with patch("tests.harness.primitive.call", return_value="after") as external:
        assert primitive.execute_now(work)
        assert not primitive.attempt_execution(work.pk, queued.token)
        assert not primitive.execute_now(work)
        external.assert_called_once()
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED


def test_a_rearm_count_at_its_column_maximum_saturates_when_no_budget_retires_the_row() -> None:
    # Unsaturated, retry raised DataError and every later reap failed the same way: the row stayed CLAIMED.
    work = record()
    PrimitiveWork.objects.filter(pk=work.pk).update(rearm_count=CLAIM_COUNTER_CEILING)
    claim = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
    assert claim is not None
    assert retry_work(claim, error_code="transient", max_rearms=None, retry_delay=timedelta(seconds=1))
    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert work.rearm_count == CLAIM_COUNTER_CEILING


@pytest.mark.parametrize("counter", ["attempt_count", "claim_generation"])
def test_a_claim_counter_at_its_column_maximum_saturates_instead_of_failing_every_claim(counter: str) -> None:
    # ARRANGE: a corrupt counter at the column's maximum; one more increment would overflow.
    work = record()
    PrimitiveWork.objects.filter(pk=work.pk).update(**{counter: CLAIM_COUNTER_CEILING})
    claim = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
    assert claim is not None, "the claim proceeds, so the work runs through its normal lifecycle"
    work.refresh_from_db()
    assert work.state == DurableWorkState.CLAIMED
    assert getattr(work, counter) == CLAIM_COUNTER_CEILING
