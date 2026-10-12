"""
Reserve before publication, consume each delivery once, and recover lost work.

Design and proof boundaries: docs/technical-design/durable-work-library.md#verification.
See the guide's verification module map for related production-bound suites.
"""

from datetime import datetime, timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from due.django.native.recovery import RecoveryLease
from due.django.runtime.coordination import database_now
from due.django.runtime.recovery_pass import RecoveryIncomplete
from due.django.testing import use_database_clock
from tests.harness.primitive import PrimitiveOwner, owed_owners
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

lease = RecoveryLease(model=PrimitiveOwner, claimed_at="claimed_at", duration=timedelta(minutes=5))
pytestmark = pytest.mark.usefixtures("isolated_primitive")


def test_recovery_excludes_outstanding_delivery_and_consumes_it_once() -> None:
    owner = PrimitiveOwner.objects.create()
    messages: list[tuple[int, datetime]] = []
    calls: list[int] = []
    due = PrimitiveOwner.objects.order_by("pk")

    def publish(identity: int, token: datetime) -> None:
        messages.append((identity, token))
        assert lease.recover(due, publish=publish, batch_size=10) == 0

    assert lease.recover(due, publish=publish, batch_size=10) == 1
    identity, token = messages[0]

    def execute(identity: int) -> None:
        calls.append(identity)
        assert lease.recover(due, publish=publish, batch_size=10) == 0
        assert not lease.attempt_execution(identity, token=token, run=calls.append)
        assert not lease.attempt_execution(identity, run=calls.append)  # Legacy delivery.

    assert lease.attempt_execution(identity, token=token, run=execute)
    assert calls == [owner.pk]
    assert not lease.attempt_execution(identity, token=token, run=calls.append)
    owner.refresh_from_db()
    assert owner.claimed_at is None


def test_lost_publication_recovers_after_expiry_without_starving_the_next_row() -> None:
    owners = PrimitiveOwner.objects.bulk_create([PrimitiveOwner(), PrimitiveOwner()])
    messages: list[tuple[int, datetime]] = []
    observed: list[int] = []
    due = PrimitiveOwner.objects.order_by("pk")

    def publish(identity: int, token: datetime) -> None:
        messages.append((identity, token))
        if identity == owners[0].pk:
            raise OSError("broker may have accepted")

    with pytest.raises(RecoveryIncomplete):  # The ambiguous publication fails its tick loudly.
        lease.recover(due, publish=publish, batch_size=1)
    assert lease.recover(due, publish=publish, batch_size=1, observe_outstanding=observed.append) == 1
    assert observed == [2]  # Includes outstanding delivery.
    assert lease.recover(due, publish=publish, batch_size=1) == 0
    old_identity, old_token = messages[0]
    PrimitiveOwner.objects.filter(pk=old_identity).update(claimed_at=timezone.now() - timedelta(minutes=6))
    with pytest.raises(RecoveryIncomplete):
        lease.recover(due, publish=publish, batch_size=1)
    assert len(messages) == 3
    calls: list[int] = []
    assert not lease.attempt_execution(old_identity, token=old_token, run=calls.append)
    assert lease.attempt_execution(old_identity, token=messages[-1][1], run=calls.append)
    assert calls == [old_identity]


def test_late_execution_cannot_release_a_successors_reservation() -> None:
    owner = PrimitiveOwner.objects.create()
    messages: list[datetime] = []

    def run(identity: int) -> None:
        PrimitiveOwner.objects.filter(pk=identity).update(claimed_at=timezone.now() - timedelta(minutes=6))
        assert (
            lease.recover(
                PrimitiveOwner.objects.order_by("pk"),
                publish=lambda identity, token: messages.append(token),
                batch_size=1,
            )
            == 1
        )

    assert lease.attempt_execution(owner.pk, run=run)
    owner.refresh_from_db()
    assert owner.claimed_at == messages[0]


_HOLD = timedelta(minutes=1)


class _Broker:
    """Records what production published; optionally loses or fails it."""

    def __init__(self, *, fail: bool = False) -> None:
        self.messages: list[tuple[int, datetime]] = []
        self.fail = fail

    def publish(self, identity: int, token: datetime) -> None:
        self.messages.append((identity, token))
        if self.fail:
            raise OSError("broker may have accepted")


def test_progress_request_publishes_after_commit_and_never_on_rollback() -> None:
    owner = PrimitiveOwner.objects.create()
    broker = _Broker()

    with pytest.raises(RuntimeError), transaction.atomic():
        lease.request_progress(owed_owners(), owner.pk, publish=broker.publish, reserve_for=_HOLD)
        raise RuntimeError("request rolled back")
    owner.refresh_from_db()
    assert broker.messages == [] and owner.claimed_at is None

    with transaction.atomic():
        lease.request_progress(owed_owners(), owner.pk, publish=broker.publish, reserve_for=_HOLD)
        assert broker.messages == []  # Nothing may observe an uncommitted request.
    owner.refresh_from_db()
    assert broker.messages == [(owner.pk, owner.claimed_at)]


def test_duplicate_progress_requests_share_one_delivery_with_the_sweep() -> None:
    owner = PrimitiveOwner.objects.create()
    broker = _Broker()

    for _ in range(3):  # Three duplicate admissions of the same obligation.
        lease.request_progress(owed_owners(), owner.pk, publish=broker.publish, reserve_for=_HOLD)
    assert lease.recover(owed_owners(), publish=broker.publish, batch_size=10) == 0
    assert len(broker.messages) == 1

    calls: list[int] = []
    _, token = broker.messages[0]
    assert lease.attempt_execution(owner.pk, token=token, run=calls.append)
    assert not lease.attempt_execution(owner.pk, token=token, run=calls.append)
    assert calls == [owner.pk]


def test_sweep_reservation_excludes_a_later_progress_request() -> None:
    owner = PrimitiveOwner.objects.create()
    broker = _Broker()

    assert lease.recover(owed_owners(), publish=broker.publish, batch_size=10) == 1
    lease.request_progress(owed_owners(), owner.pk, publish=broker.publish, reserve_for=_HOLD)
    assert len(broker.messages) == 1


def test_progress_request_cannot_reopen_settled_work() -> None:
    owner = PrimitiveOwner.objects.create(completed_at=timezone.now())
    broker = _Broker()

    lease.request_progress(owed_owners(), owner.pk, publish=broker.publish, reserve_for=_HOLD)
    owner.refresh_from_db()
    assert broker.messages == [] and owner.claimed_at is None


def test_recovery_execution_schedules_no_notifications_and_leaves_owed_work_to_the_sweep() -> None:
    owner, handed_off = PrimitiveOwner.objects.bulk_create([PrimitiveOwner(), PrimitiveOwner()])
    broker = _Broker()

    def execute(identity: int) -> None:
        # A recovery body shares the admission path: it asks for progress on
        # itself and on work it hands off to, with no "from recovery" flag.
        lease.request_progress(owed_owners(), identity, publish=broker.publish, reserve_for=_HOLD)
        lease.request_progress(owed_owners(), handed_off.pk, publish=broker.publish, reserve_for=_HOLD)

    assert lease.attempt_execution(owner.pk, run=execute)
    assert broker.messages == []
    # Positive control: outside an execution the same request publishes.
    lease.request_progress(owed_owners(), handed_off.pk, publish=broker.publish, reserve_for=_HOLD)
    assert [identity for identity, _ in broker.messages] == [handed_off.pk]
    # The released, still-owed row is the next tick's work.
    assert lease.recover(owed_owners(), publish=broker.publish, batch_size=10) == 1
    assert [identity for identity, _ in broker.messages] == [handed_off.pk, owner.pk]


def test_crash_before_notification_leaves_the_obligation_to_the_sweep() -> None:
    owner = PrimitiveOwner.objects.create()
    broker = _Broker()

    # The process dies after commit, before any callback: nothing registered.
    assert lease.recover(owed_owners(), publish=broker.publish, batch_size=10) == 1
    assert broker.messages[0][0] == owner.pk


def test_lost_progress_notification_is_redelivered_once_its_shorter_reservation_expires() -> None:
    owner = PrimitiveOwner.objects.create()
    lost = _Broker(fail=True)
    sweep = _Broker()

    lease.request_progress(owed_owners(), owner.pk, publish=lost.publish, reserve_for=_HOLD)  # Failure is contained.
    assert len(lost.messages) == 1
    # The broker may have accepted the message: no duplicate before expiry.
    assert lease.recover(owed_owners(), publish=sweep.publish, batch_size=10) == 0
    # The notification reserved for one minute, not the five-minute lease.
    almost = database_now() + _HOLD - timedelta(seconds=5)
    with use_database_clock(lambda: almost):
        assert lease.recover(owed_owners(), publish=sweep.publish, batch_size=10) == 0
    expired = database_now() + _HOLD + timedelta(seconds=1)
    with use_database_clock(lambda: expired):
        assert lease.recover(owed_owners(), publish=sweep.publish, batch_size=10) == 1

    calls: list[int] = []
    assert not lease.attempt_execution(owner.pk, token=lost.messages[0][1], run=calls.append)
    assert lease.attempt_execution(owner.pk, token=sweep.messages[0][1], run=calls.append)
    assert calls == [owner.pk]
