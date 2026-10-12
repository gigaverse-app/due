"""
``DurableScheduledAction.schedule_at``: move a pending deadline, never one whose effect may have started.

The verb a deadline adopter calls instead of hand-rolling ``atomic()`` +
``request_work(not_before=...)``: it reports an unchanged deadline instead of
writing a new revision, and it refuses (``ALREADY_STARTED``) to replace a
deadline once an attempt for the current revision may have reached the
provider, because moving it then cannot prevent the effect that escaped.
"""

from datetime import datetime, timedelta

import pytest
from django.utils import timezone

from due.django.capabilities.authoring import DurableWorkPolicy
from due.django.capabilities.scheduled import (
    CancellationResult,
    DurableScheduledAction,
    ScheduleResult,
)
from tests.harness.evidence import EvidenceWork, Provider
from tests.harness.evidence import evidence_tables as evidence_tables
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = pytest.mark.usefixtures("evidence_tables")


class Deadline(DurableScheduledAction[PrimitiveOwner, EvidenceWork]):
    def is_still_needed(self, work: EvidenceWork) -> bool:
        return True

    def perform_effect(self, work: EvidenceWork) -> None:
        Provider.write()


def _deadline() -> Deadline:
    return Deadline.for_owner(
        owner=PrimitiveOwner,
        work=EvidenceWork,
        policy=DurableWorkPolicy(retention=None, retry_delay=timedelta(0)),
    )


def _schedule(
    capability: Deadline, owner: PrimitiveOwner, *, at: datetime, replace_pending: bool = True
) -> ScheduleResult:
    return capability.schedule_at(owner.pk, EvidenceWork(universe="TapIn"), at=at, replace_pending=replace_pending)


def test_a_first_deadline_is_scheduled_and_repeating_it_writes_nothing() -> None:
    capability, owner = _deadline(), PrimitiveOwner.objects.create()
    at = timezone.now() + timedelta(hours=1)

    assert _schedule(capability, owner, at=at) == ScheduleResult.SCHEDULED
    work = EvidenceWork.objects.get(owner=owner)
    assert (work.run_at, work.cancelled) == (at, False)

    assert _schedule(capability, owner, at=at) == ScheduleResult.UNCHANGED
    assert EvidenceWork.objects.get(pk=work.pk).desired_revision == work.desired_revision


def test_a_pending_deadline_moves_to_a_new_revision() -> None:
    capability, owner = _deadline(), PrimitiveOwner.objects.create()
    first, later = timezone.now() + timedelta(hours=1), timezone.now() + timedelta(hours=2)
    _schedule(capability, owner, at=first)
    before = EvidenceWork.objects.get(owner=owner)

    assert _schedule(capability, owner, at=later) == ScheduleResult.SCHEDULED
    after = EvidenceWork.objects.get(pk=before.pk)
    assert after.run_at == later
    assert after.desired_revision > before.desired_revision


def test_replace_pending_false_keeps_the_pending_deadline() -> None:
    capability, owner = _deadline(), PrimitiveOwner.objects.create()
    first = timezone.now() + timedelta(hours=1)
    _schedule(capability, owner, at=first)

    assert _schedule(capability, owner, at=first + timedelta(hours=1), replace_pending=False) == (
        ScheduleResult.UNCHANGED
    )
    assert EvidenceWork.objects.get(owner=owner).run_at == first


def test_a_cancelled_deadline_can_be_scheduled_again() -> None:
    capability, owner = _deadline(), PrimitiveOwner.objects.create()
    at = timezone.now() + timedelta(hours=1)
    _schedule(capability, owner, at=at)
    assert capability.cancel_pending(owner.pk) == CancellationResult.CANCELLED

    assert _schedule(capability, owner, at=at) == ScheduleResult.SCHEDULED
    assert EvidenceWork.objects.get(owner=owner).cancelled is False


def test_a_deadline_whose_effect_may_have_started_is_not_moved(monkeypatch: pytest.MonkeyPatch) -> None:
    capability, owner = _deadline(), PrimitiveOwner.objects.create()
    _schedule(capability, owner, at=timezone.now())
    # schedule_at notified the worker after commit, which claimed the work; run that delivery.
    work = EvidenceWork.objects.get(owner=owner)
    # The provider times out: the request may have been applied, so the attempt stays unresolved.
    monkeypatch.setattr(Provider, "failure", TimeoutError)
    with pytest.raises(TimeoutError):
        capability.attempt_execution(work.pk, work.fencing_token)

    assert _schedule(capability, owner, at=timezone.now() + timedelta(hours=1)) == ScheduleResult.ALREADY_STARTED
    assert EvidenceWork.objects.get(pk=work.pk).run_at == work.run_at
