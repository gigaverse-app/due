"""
The primitive must work without another PR's models, provider, or tests.

That the library imports no adopter is the `shared` import contract in pyproject.toml.

Design and proof boundaries: docs/architecture.md.
See the guide's verification module map for related production-bound suites.
"""

from datetime import timedelta

import pytest
from django.db import transaction

from due.django.public import DurableWorkState
from due.django.testing import expire_lease, make_due
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
def test_lost_notification_recovers_and_settles_the_actual_product_write() -> None:
    work = record()
    assert not PrimitiveEffect.deliveries
    assert primitive.recover() == 1
    assert primitive.recover() == 0
    work_id, token = PrimitiveEffect.deliveries[0]
    assert primitive.attempt_execution(work_id, token)
    assert not primitive.attempt_execution(work_id, token)
    assert PrimitiveEffect.calls == ["after"]
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "after"
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED


@pytest.mark.usefixtures("isolated_primitive")
def test_rollback_loses_neither_half_of_the_product_and_obligation_transaction() -> None:
    owner = PrimitiveOwner.objects.create()
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            with primitive.atomic(owner.pk) as pending:
                pending.locked_owner.value = "changed"
                pending.locked_owner.save(update_fields=["value"])
                pending.request_work(PrimitiveWork(payload="after", universe="acme"))
            raise RuntimeError("rollback")
    owner.refresh_from_db()
    assert owner.value == "before"
    assert not PrimitiveWork.objects.exists()
    assert not PrimitiveEffect.deliveries


@pytest.mark.usefixtures("isolated_primitive")
def test_expired_delivery_cannot_settle_a_successors_work() -> None:
    work = record()
    primitive.recover()
    work_id, old_token = PrimitiveEffect.deliveries[0]
    expire_lease(work)
    primitive.recover()
    _, new_token = PrimitiveEffect.deliveries[-1]
    assert new_token != old_token
    assert not primitive.attempt_execution(work_id, old_token)
    assert primitive.attempt_execution(work_id, new_token)
    assert PrimitiveEffect.calls == ["after"]


@pytest.mark.usefixtures("isolated_primitive")
def test_lost_delivery_is_redelivered_without_spending_the_retry_budget() -> None:
    work = record()
    primitive.enqueue_execution(work.pk)  # Reserved and published; the broker then loses it.
    work.refresh_from_db()
    assert work.state == DurableWorkState.QUEUED
    lost_id, lost_token = PrimitiveEffect.deliveries[-1]
    expire_lease(work)
    assert primitive.recover() == 1
    work.refresh_from_db()
    assert work.rearm_count == 0, "an undelivered reservation is not a failed attempt"
    assert work.state == DurableWorkState.QUEUED
    assert work.last_error_code == ""
    assert len(PrimitiveEffect.deliveries) == 2
    assert not primitive.attempt_execution(lost_id, lost_token)
    assert primitive.attempt_execution(*PrimitiveEffect.deliveries[-1])
    assert PrimitiveEffect.calls == ["after"]


@pytest.mark.usefixtures("isolated_primitive")
def test_exhaustion_is_visible_on_the_product_and_the_same_request_rearms() -> None:
    from unittest.mock import patch

    work = record()
    owner = PrimitiveOwner.objects.get(pk=work.owner_id)
    assert primitive.policy.max_rearms is not None
    with patch("tests.harness.primitive.call", side_effect=OSError("down")):
        for _ in range(primitive.policy.max_rearms + 1):
            make_due(work)
            with pytest.raises(OSError):
                primitive.attempt_execution(work.pk)
    work.refresh_from_db()
    owner.refresh_from_db()
    assert work.state == DurableWorkState.EXHAUSTED
    assert owner.value == "exhausted", "finalize_retry must publish exhaustion where the product can see it"

    # A repeated identical requirement stays stopped; the authorized command re-arms.
    unchanged = record(owner=owner)
    assert (unchanged.pk, unchanged.state, unchanged.desired_revision) == (work.pk, DurableWorkState.EXHAUSTED, 1)
    with primitive.atomic(owner.pk, notify_worker=False) as pending:
        assert pending.retry_failed()
    rearmed = PrimitiveWork.objects.get(pk=work.pk)
    assert rearmed.state == DurableWorkState.READY
    assert rearmed.desired_revision == 2
    assert rearmed.rearm_count == 0
    make_due(work)
    assert primitive.attempt_execution(work.pk)
    owner.refresh_from_db()
    assert owner.value == "after"


@pytest.mark.usefixtures("isolated_primitive")
def test_identical_request_while_active_or_succeeded_stays_a_no_op() -> None:
    work = record()
    owner = PrimitiveOwner.objects.get(pk=work.owner_id)
    again = record(owner=owner)
    assert (again.pk, again.desired_revision, again.state) == (work.pk, 1, DurableWorkState.READY)
    primitive.recover()
    assert primitive.attempt_execution(*PrimitiveEffect.deliveries[-1])
    settled = record(owner=owner)
    assert (settled.desired_revision, settled.state) == (1, DurableWorkState.SUCCEEDED)
    with primitive.atomic(owner.pk) as pending:
        assert not pending.retry_failed(), "retry_failed re-arms only stopped failures"
    assert PrimitiveEffect.calls == ["after"]


@pytest.mark.no_db
def test_discovery_must_declare_how_its_candidate_set_is_bounded() -> None:
    from due.django.public import AppliedEvidenceContract, Discovery, DiscoveryHorizon

    common = dict(
        candidates=lambda: PrimitiveOwner.objects.order_by("pk"),
        desired_for=lambda owner, existing: None,
        page_size=10,
        applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
    )
    with pytest.raises(ValueError, match="exactly one of horizon or unbounded_because"):
        Discovery(**common)
    with pytest.raises(ValueError, match="exactly one of horizon or unbounded_because"):
        Discovery(
            **common,
            horizon=DiscoveryHorizon("created_at", timedelta(days=1)),
            unbounded_because="the owner status already limits candidates",
        )
    with pytest.raises(ValueError, match="product fact"):
        Discovery(**common, unbounded_because="fine")
    with pytest.raises(ValueError, match="positive"):
        DiscoveryHorizon("created_at", timedelta(0))
    Discovery(**common, horizon=DiscoveryHorizon("created_at", timedelta(days=1)))
    Discovery(**common, unbounded_because="candidates are live rows only; closure removes them")
