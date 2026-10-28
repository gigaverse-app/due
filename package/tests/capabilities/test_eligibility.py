"""Reserved work stays owed while ineligible; every execution route honors the gate."""

from dataclasses import replace
from datetime import timedelta

import pytest
from django.db import connection
from django.db.models import Q
from django.test.utils import CaptureQueriesContext

from due.django.public import (
    DurableWorkState,
    ExecutionEligibility,
    database_now,
    outstanding_work,
)
from due.django.testing import use_database_clock
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

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


def gated(*, recheck_after: timedelta | None = None) -> PrimitiveEffect:
    return replace(primitive, eligibility=ExecutionEligibility(Q(owner__value="ready"), recheck_after=recheck_after))


@pytest.mark.parametrize("route", ["direct", "immediate", "enqueue", "recovery"])
def test_blocked_work_retains_intent_without_executions_or_writes(route: str) -> None:
    work = record()
    capability = gated()
    with CaptureQueriesContext(connection) as queries:
        for _ in range(3):
            match route:
                case "direct":
                    assert not capability.attempt_execution(work.pk)
                case "immediate":
                    assert not capability.execute_now(work)
                case "enqueue":
                    capability.enqueue_execution(work.pk)
                case "recovery":
                    assert capability.recover() == 0
    assert not [q["sql"] for q in queries if q["sql"].startswith(("UPDATE", "INSERT", "DELETE"))]
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY
    assert (work.attempt_count, work.rearm_count, work.desired_revision) == (0, 0, 1)
    assert outstanding_work(PrimitiveWork).filter(pk=work.pk).exists()
    assert PrimitiveEffect.calls == PrimitiveEffect.deliveries == []


def test_eligibility_filters_before_page_limit_and_recovers_lost_notification() -> None:
    for _ in range(5):
        record()
    ready = record()
    capability = replace(gated(), policy=replace(primitive.policy, batch_size=1))
    PrimitiveOwner.objects.filter(pk=ready.owner_id).update(value="ready")
    assert capability.recover() == 1
    work_id, token = PrimitiveEffect.deliveries.pop()
    assert work_id == ready.pk
    assert capability.attempt_execution(work_id, token)
    ready.refresh_from_db()
    assert ready.state == DurableWorkState.SUCCEEDED
    assert PrimitiveEffect.calls == [ready.payload]


def test_delivery_rechecks_gate_and_releases_reservation_without_spending_retry_budget() -> None:
    work = record()
    capability = gated()
    PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="ready")
    capability.enqueue_execution(work.pk)
    _, old_token = PrimitiveEffect.deliveries.pop()
    PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="waiting")
    assert not capability.attempt_execution(work.pk, old_token)
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY
    assert work.lease_expires_at is None
    assert work.fencing_token != old_token
    assert (work.attempt_count, work.rearm_count, work.desired_revision) == (1, 0, 1)
    PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="ready")
    assert not capability.attempt_execution(work.pk, old_token)
    assert capability.recover() == 1
    _, new_token = PrimitiveEffect.deliveries.pop()
    assert not capability.attempt_execution(work.pk, old_token)
    work.refresh_from_db()
    assert work.state == DurableWorkState.QUEUED
    assert work.fencing_token == new_token
    assert (work.attempt_count, work.rearm_count, work.desired_revision) == (2, 0, 1)
    assert capability.attempt_execution(work.pk, new_token)
    assert PrimitiveEffect.calls == [work.payload]


def test_periodic_fallback_is_opt_in_and_never_bypasses_not_before() -> None:
    work = record()
    now = database_now()
    due_at = now + timedelta(minutes=2)
    PrimitiveWork.objects.filter(pk=work.pk).update(available_at=due_at)
    capability = gated(recheck_after=timedelta(minutes=1))
    with use_database_clock(lambda: due_at - timedelta(seconds=1)):
        assert not capability.attempt_execution(work.pk)
    with use_database_clock(lambda: due_at + timedelta(seconds=59)):
        assert capability.recover() == 0
    with use_database_clock(lambda: due_at + timedelta(minutes=1)):
        assert capability.attempt_execution(work.pk)
    assert PrimitiveEffect.calls == [work.payload]


@pytest.mark.parametrize("queued", [False, True], ids=["worker-death", "lost-delivery"])
def test_reaping_is_not_blocked_by_lost_eligibility(queued: bool) -> None:
    from due.django.public import claim_work
    from due.django.testing import expire_lease

    work = record()
    PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="ready")
    capability = gated()
    assert (
        claim_work(
            PrimitiveWork,
            work.pk,
            lease_duration=capability.policy.lease_duration,
            eligibility=capability.eligibility,
            queued=queued,
        )
        is not None
    )
    PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="waiting")
    expire_lease(work)
    assert capability.recover() == 0
    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert work.lease_expires_at is None
    assert outstanding_work(PrimitiveWork).filter(pk=work.pk).exists()
    assert PrimitiveEffect.calls == []


def test_deleted_nullable_owner_can_be_selected_for_cleanup() -> None:
    work = record()
    PrimitiveOwner.objects.filter(pk=work.owner_id).delete()
    capability = replace(primitive, eligibility=ExecutionEligibility(lambda now: Q(owner__isnull=True)))
    assert capability.attempt_execution(work.pk)
    assert PrimitiveEffect.calls == [work.payload]


def test_periodic_continuation_resets_inspection_without_spending_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from due.django.public import ContinueAfter

    work = record()
    capability = gated(recheck_after=timedelta(minutes=1))
    now = database_now()
    with use_database_clock(lambda: now + timedelta(minutes=1)):
        monkeypatch.setattr(
            PrimitiveEffect, "perform_effect", lambda self, current: ContinueAfter(timedelta(seconds=3), until=None)
        )
        assert capability.attempt_execution(work.pk)
    with use_database_clock(lambda: now + timedelta(minutes=2)):
        assert not capability.attempt_execution(work.pk)
    with use_database_clock(lambda: now + timedelta(minutes=2, seconds=3)):
        assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert (work.attempt_count, work.rearm_count, work.desired_revision) == (2, 0, 1)


@pytest.mark.parametrize(
    "condition,recheck_after,eligible,waiting",
    [
        (Q(owner__value="ready"), None, 1, 2),
        (~Q(owner__value="ready"), None, 2, 1),
        (Q(owner__value="ready"), timedelta(minutes=1), 3, 0),
        (Q(), None, 3, 0),
        (Q(pk__in=[]), None, 0, 3),
    ],
    ids=["nullable-join", "negated-nullable-join", "fallback", "always", "never"],
)
def test_census_distinguishes_waiting_from_eligible_without_hiding_obligations(
    condition: Q, recheck_after: timedelta | None, eligible: int, waiting: int
) -> None:
    from unittest.mock import patch

    from due.django.runtime import coordination

    record()
    ready = record()
    orphan = record()
    delayed = record()
    now = database_now()
    PrimitiveWork.objects.update(available_at=now - timedelta(minutes=1))
    PrimitiveWork.objects.filter(pk=delayed.pk).update(available_at=now + timedelta(hours=1))
    PrimitiveOwner.objects.filter(pk=ready.owner_id).update(value="ready")
    PrimitiveOwner.objects.filter(pk=orphan.owner_id).delete()
    eligibility = ExecutionEligibility(lambda now: condition, recheck_after=recheck_after)
    with (
        patch.object(coordination, "record_execution_eligibility") as metric,
        patch.object(coordination, "record_outstanding") as total,
    ):
        coordination.publish_work_census(PrimitiveWork, now=lambda: now, eligibility=eligibility)
    metric.assert_called_once_with(PrimitiveWork._meta.label_lower, eligible=eligible, waiting=waiting)
    total.assert_called_once_with(PrimitiveWork._meta.label_lower, 4)


@pytest.mark.parametrize("rollback", [False, True])
def test_readiness_progress_is_commit_bound_and_recovers_a_lost_nudge(rollback: bool) -> None:
    from django.db import transaction

    from due.django.testing import hold_nudges

    work = record()
    capability = gated()
    assert work.owner_id is not None
    with hold_nudges() as held:
        with transaction.atomic():
            with capability.atomic(work.owner_id) as pending:
                pending.locked_owner.value = "ready"
                pending.locked_owner.save(update_fields=["value"])
                assert pending.request_progress()
                assert held == []
            if rollback:
                transaction.set_rollback(True)
    assert len(held) == (0 if rollback else 1)
    work.refresh_from_db()
    assert (work.attempt_count, work.rearm_count, work.desired_revision) == (0, 0, 1)
    assert capability.recover() == (0 if rollback else 1)
    if not rollback:
        _, token = PrimitiveEffect.deliveries.pop()
        assert capability.attempt_execution(work.pk, token)
        assert PrimitiveEffect.calls == [work.payload]


def test_progress_preserves_backoff_and_cannot_rearm_stopped_work() -> None:
    work = record()
    capability = gated()
    assert work.owner_id is not None
    PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="ready")
    due_at = database_now() + timedelta(hours=1)
    PrimitiveWork.objects.filter(pk=work.pk).update(available_at=due_at)
    with capability.atomic(work.owner_id) as pending:
        assert pending.request_progress()
    work.refresh_from_db()
    assert work.available_at == due_at
    assert work.attempt_count == 0
    assert PrimitiveEffect.deliveries == []
    with use_database_clock(lambda: due_at):
        assert capability.attempt_execution(work.pk)
    with capability.atomic(work.owner_id) as pending:
        assert not pending.request_progress()
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.desired_revision == 1


def test_contract_gate_preserves_the_sweep_occurrence_identity() -> None:
    from contextlib import contextmanager

    from due.django.conformance._internal.suite import eligibility_binding
    from due.django.conformance.registrations import durable_work_installed, registration_of
    from due.django.conformance.suite import DeliveryExample, EffectExamples, EligibilityExample

    # Distinct identities expose a mismatch that equal integer sequences hide.
    PrimitiveOwner.objects.create()
    work = record()
    assert work.owner_id != work.pk

    def ready() -> None:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="ready")

    def delivery():
        return DeliveryExample(
            work,
            observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
            expected=work.payload,
            effect_calls=lambda: len(PrimitiveEffect.calls),
        )

    @contextmanager
    def example():
        yield EligibilityExample(delivery=delivery(), make_eligible=ready)

    examples = EffectExamples(make_work=record, delivery=delivery, identity_of=lambda row: row.owner_id)
    capability = gated()
    entry = replace(registration_of(PrimitiveWork), capability=capability)
    with durable_work_installed(entry), eligibility_binding(capability, examples, example) as gate:
        assert gate.identity == work.owner_id
        assert list(gate.owed_work()) == [work.owner_id]
        assert list(gate.due_work()) == []
        gate.make_eligible()
        assert list(gate.due_work()) == [work.owner_id]
        gate.recover()
        assert gate.observe() == work.payload
        assert gate.effect_calls() == 1
