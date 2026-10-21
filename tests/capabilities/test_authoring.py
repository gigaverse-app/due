"""
Owner-bound authoring invariants, independent of any application model.

Design and usage: docs/architecture.md.
"""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.utils import timezone

from due._installed import NotInstalledError
from due.django.public import (
    AppliedEvidenceContract,
    Discovery,
    DurableWorkState,
    claim_work,
    complete_work,
    prune_completed_work,
    retry_work,
)
from due.django.runtime.recovery_pass import RecoveryPass
from due.django.testing import expire_lease, hold_nudges
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

POLICY = primitive.policy

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


def _desired(work):
    return PrimitiveWork(payload=work.payload, universe=work.universe)


def test_unchanged_reconciliation_preserves_completed_revision_and_budget():
    work = record()
    assert work.owner_id is not None

    claim = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
    assert claim is not None
    assert complete_work(claim)
    capability = replace(
        primitive,
        policy=replace(POLICY, retention=None),
        discovery=Discovery(
            unbounded_because="Only explicitly selected synthetic fixture owners are candidates.",
            candidates=lambda: PrimitiveOwner.objects.filter(pk=work.owner_id).order_by("pk"),
            desired_for=lambda room, existing: _desired(work),
            page_size=10,
            applied_evidence=AppliedEvidenceContract.RETAINED_WORK_ROW,
        ),
    )
    with hold_nudges() as nudges:
        for _ in range(3):
            assert capability.derive() == 1
        assert not nudges
    work.refresh_from_db()
    assert (work.state, work.desired_revision, work.applied_revision, work.rearm_count) == (
        DurableWorkState.SUCCEEDED,
        1,
        1,
        0,
    )


def test_discovery_owns_page_slicing_and_never_evaluates_the_fourth_owner():
    work = [record() for _ in range(4)]
    by_owner: dict[int, PrimitiveWork] = {}
    for item in work:
        assert item.owner_id is not None
        by_owner[item.owner_id] = item
    seen = []

    def desired_for(room, existing):
        seen.append(room.pk)
        return _desired(by_owner[room.pk])

    capability = replace(
        primitive,
        discovery=Discovery(
            unbounded_because="Only explicitly selected synthetic fixture owners are candidates.",
            candidates=lambda: PrimitiveOwner.objects.filter(pk__in=by_owner).order_by("pk"),
            desired_for=desired_for,
            page_size=3,
            applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
        ),
    )
    assert capability.derive() == 3
    assert seen == sorted(by_owner)[:3]
    assert capability.undiscovered_count() == 4


def test_discovery_supplies_the_work_row_already_locked_with_its_owner():
    work = record()
    assert work.owner_id is not None
    observed = []

    def desired(owner, existing):
        assert existing is not None
        assert existing.owner_id == owner.pk
        observed.append((existing.pk, existing.payload))
        return _desired(existing)

    capability = replace(
        primitive,
        discovery=Discovery(
            candidates=lambda: PrimitiveOwner.objects.filter(pk=work.owner_id).order_by("pk"),
            desired_for=desired,
            page_size=1,
            applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
            unbounded_because="Only the explicitly selected fixture owner is a candidate.",
        ),
    )
    assert capability.derive() == 1
    assert observed == [(work.pk, work.payload)]
    work.refresh_from_db()
    assert work.desired_revision == 1


def test_callback_does_not_exist_is_not_mistaken_for_vanished_owner():
    work = record()
    assert work.owner_id is not None

    def broken_derivation(room, existing):
        raise PrimitiveOwner.DoesNotExist("callback dependency missing")

    capability = replace(
        primitive,
        discovery=Discovery(
            unbounded_because="Only explicitly selected synthetic fixture owners are candidates.",
            candidates=lambda: PrimitiveOwner.objects.filter(pk=work.owner_id).order_by("pk"),
            desired_for=broken_derivation,
            page_size=3,
            applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
        ),
    )

    with pytest.raises(ExceptionGroup) as raised:
        capability.derive()
    assert raised.group_contains(PrimitiveOwner.DoesNotExist, match="callback dependency")


def test_retention_does_not_erase_exhaustion_evidence():
    work = record()

    claim = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
    assert claim is not None
    retry_work(claim, error_code="unavailable", max_rearms=0, retry_delay=timedelta(0))
    PrimitiveWork.objects.filter(pk=work.pk).update(completed_at=timezone.now() - timedelta(days=30))
    assert prune_completed_work(PrimitiveWork, completed_before=timezone.now()) == 0
    work.refresh_from_db()
    assert work.state == DurableWorkState.EXHAUSTED


def test_typed_input_cannot_override_coordination_or_cross_owners():
    first, second = record(), record()
    assert first.owner_id is not None
    invalid = _desired(first)
    invalid.rearm_count = 9
    with primitive.atomic(first.owner_id) as change, pytest.raises(TypeError, match="kernel-owned"):
        change.request_work(invalid)
    invalid = _desired(first)
    invalid.owner_id = second.owner_id
    with primitive.atomic(first.owner_id) as change, pytest.raises(TypeError, match="different owner"):
        change.request_work(invalid)
    first.refresh_from_db()
    assert first.rearm_count == 0
    assert first.desired_revision == 1


def test_scheduling_a_future_job_preserves_its_first_deadline_when_repeated():
    previous = record()
    owner_id = previous.owner_id
    assert owner_id is not None
    previous.delete()
    deadline = timezone.now() + timedelta(hours=1)
    with primitive.atomic(owner_id) as change:
        work = change.request_work(_desired(previous), not_before=deadline)
        assert change.locked_owner.value == "before"  # Scheduling did not mutate it.
    assert not primitive.attempt_execution(work.pk)
    with primitive.atomic(owner_id) as change:
        repeated = change.request_work(_desired(previous), not_before=deadline + timedelta(hours=1))
    assert repeated.pk == work.pk
    assert repeated.available_at == deadline
    assert repeated.attempt_count == 0


def test_discovery_cannot_prune_its_only_completion_evidence():
    with pytest.raises(ValueError, match="must disable retention"):
        replace(
            primitive,
            discovery=Discovery(
                unbounded_because="Only explicitly selected synthetic fixture owners are candidates.",
                candidates=lambda: PrimitiveOwner.objects.none(),
                desired_for=lambda room, existing: None,
                page_size=3,
                applied_evidence=AppliedEvidenceContract.RETAINED_WORK_ROW,
            ),
        )


def _discovery_with_poisoned_owner(
    candidates: list[PrimitiveOwner], poisoned: PrimitiveOwner | None
) -> Discovery[PrimitiveOwner, PrimitiveWork]:
    def desired_for(owner: PrimitiveOwner, existing: PrimitiveWork | None) -> PrimitiveWork:
        # An empty payload fails full_clean, as a source-less image's blank
        # source_key did on dev: the owner's fact can never become valid work.
        payload = "" if poisoned is not None and owner.pk == poisoned.pk else "derived"
        return PrimitiveWork(payload=payload, universe="acme")

    return Discovery(
        unbounded_because="Only explicitly selected synthetic fixture owners are candidates.",
        candidates=lambda: PrimitiveOwner.objects.filter(pk__in=[owner.pk for owner in candidates]).order_by("pk"),
        desired_for=desired_for,
        page_size=10,
        applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
    )


@pytest.mark.parametrize("poison", [True, False], ids=["poisoned-owner", "control"])
def test_discovery_owner_failing_validation_does_not_stop_recovery_of_other_owed_work(poison: bool):
    recorded = record(notify_worker=False)  # Owed work only recovery will dispatch.
    poisoned, later = PrimitiveOwner.objects.create(), PrimitiveOwner.objects.create()
    capability = replace(
        primitive,
        discovery=_discovery_with_poisoned_owner([poisoned, later], poisoned if poison else None),
    )

    if poison:
        with pytest.raises(ExceptionGroup) as raised:
            capability.recover()
        assert raised.group_contains(ValidationError, match="cannot be blank")
        assert len(raised.value.exceptions) == 1
        assert not PrimitiveWork.objects.filter(owner=poisoned).exists()
    else:
        capability.recover()
        assert PrimitiveWork.objects.filter(owner=poisoned, payload="derived").exists()

    # The same pass still derived the owner after the poisoned one and
    # dispatched both it and the already-recorded work; the control run proves
    # these are the deliveries an unpoisoned pass makes.
    derived = PrimitiveWork.objects.get(owner=later, payload="derived")
    delivered = {work_id for work_id, _ in PrimitiveEffect.deliveries}
    assert {recorded.pk, derived.pk} <= delivered
    assert len(delivered) == (2 if poison else 3)


def test_unwired_slot_in_discovery_ends_the_pass_instead_of_being_isolated():
    first, later = PrimitiveOwner.objects.create(), PrimitiveOwner.objects.create()

    def unwired(owner: PrimitiveOwner, existing: PrimitiveWork | None) -> PrimitiveWork:
        raise NotInstalledError("membership is not installed")

    capability = replace(
        primitive,
        discovery=Discovery(
            unbounded_because="Only explicitly selected synthetic fixture owners are candidates.",
            candidates=lambda: PrimitiveOwner.objects.filter(pk__in=[first.pk, later.pk]).order_by("pk"),
            desired_for=unwired,
            page_size=10,
            applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
        ),
    )
    # A deployment defect shared by every owner surfaces bare, not as one more isolated failure.
    with pytest.raises(NotInstalledError):
        capability.recover()


def test_a_whole_failing_phase_is_isolated_and_reported_with_the_rest():
    recorded = record(notify_worker=False)
    poisoned = PrimitiveOwner.objects.create()
    capability = replace(primitive, discovery=_discovery_with_poisoned_owner([poisoned], poisoned))
    with (
        patch(
            "due.django.capabilities.authoring.reap_expired_work",
            side_effect=RuntimeError("reaper down"),
        ),
        patch("due.django.runtime.recovery_pass.record_recovery_completed") as heartbeat,
        pytest.raises(ExceptionGroup) as raised,
    ):
        capability.recover()
    # A broken reap phase did not stop dispatch, and both failures are reported once.
    assert recorded.pk in {work_id for work_id, _ in PrimitiveEffect.deliveries}
    assert raised.group_contains(RuntimeError, match="reaper down")
    assert raised.group_contains(ValidationError, match="cannot be blank")
    assert any("phase=reap" in note for note in raised.value.exceptions[-1].__notes__)
    heartbeat.assert_not_called()  # Only a clean tick counts as completed recovery.


def test_a_broken_candidate_query_does_not_take_the_recorded_work_census_down():
    record(notify_worker=False)

    def candidates() -> QuerySet[PrimitiveOwner]:
        raise RuntimeError("relation does not exist")

    capability = replace(primitive, discovery=replace(_discovery_with_poisoned_owner([], None), candidates=candidates))
    with (
        patch("due.django.runtime.coordination.record_outstanding") as outstanding,
        patch("due.django.runtime.recovery_pass.record_recovery_deferrals") as deferrals,
        pytest.raises(ExceptionGroup) as raised,
    ):
        capability.recover()
    assert raised.group_contains(RuntimeError, match="relation does not exist")
    outstanding.assert_called_once_with(PrimitiveWork._meta.label_lower, 1)
    assert deferrals.called


def test_a_tick_cannot_declare_one_step_key_twice():
    with pytest.raises(ValueError, match="repeats a step key"):
        primitive._run_steps((*primitive.recorded_work_steps(), *primitive.recorded_work_steps()), RecoveryPass("x"))


def test_a_clean_tick_records_the_heartbeat():
    record(notify_worker=False)
    with patch("due.django.runtime.recovery_pass.record_recovery_completed") as heartbeat:
        assert primitive.recover() == 1
    heartbeat.assert_called_once()


def test_celery_soft_time_limit_ends_the_tick_instead_of_being_isolated():
    from celery.exceptions import SoftTimeLimitExceeded

    first, later = PrimitiveOwner.objects.create(), PrimitiveOwner.objects.create()
    seen: list[int] = []

    def out_of_time(owner: PrimitiveOwner, existing: PrimitiveWork | None) -> PrimitiveWork:
        seen.append(owner.pk)
        raise SoftTimeLimitExceeded()

    capability = replace(
        primitive,
        discovery=Discovery(
            unbounded_because="Only explicitly selected synthetic fixture owners are candidates.",
            candidates=lambda: PrimitiveOwner.objects.filter(pk__in=[first.pk, later.pk]).order_by("pk"),
            desired_for=out_of_time,
            page_size=10,
            applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
        ),
    )
    # Celery asks the task to stop: it must not move on to the next owner toward the hard kill.
    with pytest.raises(SoftTimeLimitExceeded):
        capability.recover()
    assert seen == [first.pk]


def test_dispatch_limit_does_not_reduce_recovery_page():
    capability = replace(primitive, policy=replace(POLICY, batch_size=10, dispatch_batch_size=1))
    settled = [record() for _ in range(11)]
    for work in settled:
        claim = claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration)
        assert claim is not None and complete_work(claim)
    PrimitiveWork.objects.filter(pk__in=[work.pk for work in settled]).update(
        completed_at=timezone.now() - timedelta(days=8)
    )
    expired = [record() for _ in range(10)]
    for work in expired:
        assert claim_work(PrimitiveWork, work.pk, lease_duration=POLICY.lease_duration) is not None
        expire_lease(work)
    with patch.object(type(capability), "nudge") as publish:
        capability.recover()
    for work in expired:
        work.refresh_from_db()
        assert work.rearm_count == 1
    assert sum(work.state == DurableWorkState.QUEUED for work in expired) == 1
    assert sum(work.state == DurableWorkState.RETRYABLE for work in expired) == 9
    assert publish.call_count == 1
    assert PrimitiveWork.objects.filter(pk__in=[work.pk for work in settled]).count() == 1


@pytest.mark.parametrize("limit", [0, -1])
def test_dispatch_limit_must_be_positive(limit):
    with pytest.raises(ValueError, match="dispatch_batch_size"):
        replace(POLICY, dispatch_batch_size=limit)
