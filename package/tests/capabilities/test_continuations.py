"""
Expected waits release leases without resetting revision or failure history.

Design and proof boundaries: docs/architecture.md.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from due.django.public import (
    ContinueAfter,
    DurableWorkState,
    claim_work,
    database_now,
    supersede_work,
)
from due.django.runtime.coordination import continue_work, retry_work
from due.django.testing import make_due
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


def test_waiting_releases_ownership_without_success_or_failure_budget_reset():
    work = record()
    for _ in range(2):
        claim = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
        assert claim is not None
        assert retry_work(claim, error_code="injected", max_rearms=3, retry_delay=timedelta(0))
        make_due(work)
    with (
        patch(
            "tests.harness.primitive.PrimitiveEffect.apply",
            return_value=ContinueAfter(timedelta(minutes=2), until=None),
        ),
        patch("tests.harness.primitive.PrimitiveEffect.finalize_success") as finalize,
    ):
        assert primitive.attempt_execution(work.pk)
        finalize.assert_not_called()
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY
    assert work.completed_at is work.applied_revision is work.lease_expires_at is None
    assert work.available_at > timezone.now()
    assert (work.rearm_count, work.desired_revision, work.attempt_count) == (2, 1, 3)
    assert not primitive.attempt_execution(work.pk)


def test_continuation_cannot_overwrite_a_newer_revision():
    work = record()
    claim = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
    assert claim is not None
    supersede_work(PrimitiveWork, work.pk)
    assert not continue_work(claim, ContinueAfter(timedelta(days=1), until=None))
    work.refresh_from_db()
    assert work.desired_revision == 2
    # supersede_work stamps available_at on PostgreSQL's clock; compare on that clock.
    assert work.available_at <= database_now()


def test_expired_continuation_deadline_retains_exhaustion_evidence():
    work = record()
    with patch(
        "tests.harness.primitive.PrimitiveEffect.apply",
        return_value=ContinueAfter(timedelta(minutes=1), until=timezone.now() - timedelta(seconds=1)),
    ):
        assert primitive.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.EXHAUSTED
    assert work.last_error_code == "continuation_deadline"
    assert work.completed_at is not None


def test_future_deadline_limits_the_next_wakeup():
    work = record()
    deadline = timezone.now() + timedelta(minutes=1)
    with patch(
        "tests.harness.primitive.PrimitiveEffect.apply",
        return_value=ContinueAfter(timedelta(days=1), until=deadline),
    ):
        assert primitive.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.available_at == deadline


def test_expired_continuation_publishes_exhaustion_in_the_same_transaction(monkeypatch):
    from due.django.public import StatusMirror
    from tests.harness.primitive import PrimitiveEffect, PrimitiveOwner

    mirror = StatusMirror(lambda work: PrimitiveOwner.objects.filter(pk=work.owner_id))
    observed = []

    def after_retry(self, work, error):
        observed.append(PrimitiveOwner.objects.get(pk=work.owner_id).value)

    monkeypatch.setattr(PrimitiveEffect, "finalize_retry", after_retry)
    monkeypatch.setattr(PrimitiveEffect, "on_exhausted", mirror.outcome({"value": "deadline expired"}))
    monkeypatch.setattr(
        PrimitiveEffect,
        "apply",
        lambda *args: ContinueAfter(timedelta(seconds=1), until=timezone.now() - timedelta(seconds=1)),
    )
    work = record()
    assert primitive.attempt_execution(work.pk)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "deadline expired"
    assert observed == ["deadline expired"]
    work.refresh_from_db()
    assert work.state == DurableWorkState.EXHAUSTED
    assert work.rearm_count == 0


def test_failed_deadline_settlement_rolls_back_work_and_product():
    from tests.harness.primitive import PrimitiveOwner

    work = record()
    claim = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
    assert claim is not None

    def finalize(current):
        PrimitiveOwner.objects.filter(pk=current.owner_id).update(value="must roll back")
        raise ValueError("product settlement failed")

    with pytest.raises(ValueError, match="product settlement failed"):
        continue_work(
            claim,
            ContinueAfter(timedelta(seconds=1), until=timezone.now() - timedelta(seconds=1)),
            finalize_expired=finalize,
        )
    work.refresh_from_db()
    assert work.state == DurableWorkState.CLAIMED
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"
