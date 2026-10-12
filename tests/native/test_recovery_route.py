"""
A bound route asks for progress by identity and keeps every RecoveryLease guarantee.

Publication happens after the outermost commit on the transaction's thread, carries
the reservation token the worker consumes once, and a lost notification costs no
more than its countdown plus the declared slack before the sweep redelivers.

Design and usage: docs/technical-design/durable-work-library.md#offer-an-admitted-obligation-progress.
"""

from contextlib import nullcontext
from copy import replace
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from threading import get_ident

import pytest
from asgiref.sync import async_to_sync
from celery import Celery, shared_task
from django.db import connection, transaction
from django.utils import timezone

from due.django.native.recovery import RecoveryLease, RecoveryRoute
from due.django.runtime import transport
from due.django.runtime.coordination import database_now
from due.django.runtime.transport import run_effect_coroutine
from due.django.testing import HeldNudge, hold_nudges, use_database_clock
from tests.harness.primitive import PrimitiveOwner, owed_owners
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = pytest.mark.usefixtures("isolated_primitive")

_SLACK = timedelta(minutes=1)


@shared_task(ignore_result=True, time_limit=60)
def check_owner(identity: int, delivery_token: str | None = None) -> None:
    """A worker that would consume the delivery; tests record its publication instead."""


route = RecoveryRoute(
    lease=RecoveryLease(model=PrimitiveOwner, claimed_at="claimed_at", duration=timedelta(minutes=5)),
    owed=owed_owners,
    task=check_owner,
    reservation_slack=_SLACK,
    max_delay=timedelta(minutes=2),
)


@dataclass(frozen=True)
class Published:
    identity: int
    token: str
    countdown: float | None
    thread: int
    inside_transaction: bool


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> list[Published]:
    """Record the task boundary, and whether a transaction was still open there."""
    published: list[Published] = []

    def apply_async(*, args: tuple[int, str], countdown: float | None = None) -> None:
        published.append(Published(*args, countdown, get_ident(), connection.in_atomic_block))

    monkeypatch.setattr(check_owner, "apply_async", apply_async)
    return published


def _request_sync(identity: int, delay: timedelta = timedelta(0)) -> None:
    route.request_progress(identity, delay=delay)


def _request_async(identity: int, delay: timedelta = timedelta(0)) -> None:
    async_to_sync(route.arequest_progress)(identity, delay=delay)


CALLERS = pytest.mark.parametrize("request_progress", [_request_sync, _request_async], ids=["sync", "async"])


@CALLERS
def test_progress_publishes_its_reservation_after_the_outermost_commit(broker, request_progress) -> None:
    owner = PrimitiveOwner.objects.create()

    with transaction.atomic():
        with transaction.atomic():
            request_progress(owner.pk, timedelta(seconds=15))
        assert broker == []  # Nothing may observe an uncommitted request.

    owner.refresh_from_db()
    assert owner.claimed_at is not None
    assert broker == [Published(owner.pk, owner.claimed_at.isoformat(), 15.0, get_ident(), inside_transaction=False)]


@CALLERS
def test_rollback_reserves_and_publishes_nothing(broker, request_progress) -> None:
    owner = PrimitiveOwner.objects.create()

    with pytest.raises(RuntimeError, match="rollback"), transaction.atomic():
        request_progress(owner.pk)
        raise RuntimeError("rollback")

    owner.refresh_from_db()
    assert broker == [] and owner.claimed_at is None


def test_the_worker_consumes_a_published_token_once(broker) -> None:
    owner = PrimitiveOwner.objects.create()
    route.request_progress(owner.pk)
    route.request_progress(owner.pk)  # A duplicate admission shares the delivery.
    assert [publication.countdown for publication in broker] == [None]

    runs: list[int] = []
    token = broker[0].token
    assert route.attempt_delivery(owner.pk, token, run=runs.append)
    assert not route.attempt_delivery(owner.pk, token, run=runs.append)
    assert runs == [owner.pk]


def test_a_lost_notification_is_redelivered_after_its_countdown_plus_slack(broker) -> None:
    owner = PrimitiveOwner.objects.create()
    delay = timedelta(seconds=30)
    route.request_progress(owner.pk, delay=delay)
    lost = broker.pop()

    # The broker may still deliver the delayed message: no duplicate until then.
    assert route.recover(owed_owners(), batch_size=10) == 0
    almost = database_now() + delay + _SLACK - timedelta(seconds=5)
    with use_database_clock(lambda: almost):
        assert route.recover(owed_owners(), batch_size=10) == 0
    expired = database_now() + delay + _SLACK + timedelta(seconds=1)
    with use_database_clock(lambda: expired):
        assert route.recover(owed_owners(), batch_size=10) == 1

    runs: list[int] = []
    assert not route.attempt_delivery(owner.pk, lost.token, run=runs.append)
    assert route.attempt_delivery(owner.pk, broker[0].token, run=runs.append)
    assert runs == [owner.pk]


def test_progress_requested_inside_its_own_delivery_publishes_nothing(broker) -> None:
    owner, handed_off = PrimitiveOwner.objects.bulk_create([PrimitiveOwner(), PrimitiveOwner()])

    def execute(identity: int) -> None:
        # Recovery reuses the admission path with no "entered from recovery" flag.
        route.request_progress(identity, delay=timedelta(seconds=15))
        route.request_progress(handed_off.pk)

    assert route.attempt_delivery(owner.pk, None, run=execute)
    assert broker == []
    # Positive control: outside a delivery the same request publishes.
    route.request_progress(handed_off.pk)
    assert [publication.identity for publication in broker] == [handed_off.pk]


def test_settled_work_is_never_reopened(broker) -> None:
    owner = PrimitiveOwner.objects.create(completed_at=timezone.now())
    route.request_progress(owner.pk)
    owner.refresh_from_db()
    assert broker == [] and owner.claimed_at is None


def test_a_held_notification_leaves_the_row_to_the_sweep_at_once(broker) -> None:
    owner = PrimitiveOwner.objects.create()

    with hold_nudges() as held:
        route.request_progress(owner.pk, delay=timedelta(seconds=15))

    owner.refresh_from_db()
    assert held == [HeldNudge("tests.primitiveowner.claimed_at", owner.pk)]
    assert broker == [] and owner.claimed_at is None
    assert route.recover(owed_owners(), batch_size=10) == 1
    assert [(publication.identity, publication.countdown) for publication in broker] == [(owner.pk, None)]


def test_a_broker_failure_is_observable_and_keeps_its_reservation(monkeypatch, mocker) -> None:
    owner = PrimitiveOwner.objects.create()

    def broker_unavailable(*, args: tuple[int, str], countdown: float | None = None) -> None:
        raise ConnectionError("broker may have accepted")

    monkeypatch.setattr(check_owner, "apply_async", broker_unavailable)
    logged = mocker.patch.object(transport.logger, "exception")

    route.request_progress(owner.pk)

    logged.assert_called_once_with(
        "Durable-work post-commit notification failed",
        extra={"effect": "tests.primitiveowner.claimed_at", "work_id": owner.pk},
    )
    owner.refresh_from_db()
    assert owner.claimed_at is not None
    assert route.recover(owed_owners(), batch_size=10) == 0  # No duplicate while it may be queued.


def test_a_delay_must_be_a_countdown_within_the_declared_maximum(broker) -> None:
    owner = PrimitiveOwner.objects.create()
    for delay in (timedelta(seconds=-1), route.max_delay + timedelta(seconds=1)):
        with pytest.raises(ValueError, match="max_delay"):
            route.request_progress(owner.pk, delay=delay)
    assert broker == []


@pytest.mark.parametrize(
    ("slack", "max_delay"),
    [
        (timedelta(0), timedelta(0)),
        (timedelta(minutes=1), timedelta(seconds=-1)),
        (timedelta(minutes=1), timedelta(minutes=4, seconds=1)),
    ],
    ids=["no-slack", "negative-delay", "exceeds-lease"],
)
def test_a_route_that_could_outlive_its_lease_is_rejected_at_import(slack: timedelta, max_delay: timedelta) -> None:
    lease = RecoveryLease(model=PrimitiveOwner, claimed_at="claimed_at", duration=timedelta(minutes=5))
    with pytest.raises(ValueError):
        RecoveryRoute(lease=lease, owed=owed_owners, task=check_owner, reservation_slack=slack, max_delay=max_delay)


def test_an_async_recovery_body_publishes_nothing(broker) -> None:
    """The adopter shape: an async body run inside the delivery asks for progress."""
    owner, handed_off = PrimitiveOwner.objects.bulk_create([PrimitiveOwner(), PrimitiveOwner()])

    async def body(identity: int) -> None:
        await route.arequest_progress(identity, delay=timedelta(seconds=15))
        await route.arequest_progress(handed_off.pk)

    assert route.attempt_delivery(owner.pk, None, run=partial(run_effect_coroutine, body))
    assert broker == []
    route.request_progress(handed_off.pk)  # Positive control.
    assert [publication.identity for publication in broker] == [handed_off.pk]


def test_legacy_tokenless_messages_acquire_the_same_lease(broker) -> None:
    owner = PrimitiveOwner.objects.create()
    runs: list[int] = []
    assert route.attempt_delivery(owner.pk, None, run=runs.append)
    owner.refresh_from_db()
    assert runs == [owner.pk] and owner.claimed_at is None


@pytest.mark.parametrize(
    ("hard_limit", "soft_limit", "autoretry_for", "reason"),
    [
        (60, None, (Exception,), "autoretry_for"),
        (None, None, (), "time_limit"),
        (0, None, (), "time_limit"),
        (-1, None, (), "time_limit"),
        (300, None, (), "time_limit"),
        (301, None, (), "time_limit"),
        (60, 61, (), "soft_time_limit"),
        (60, None, (), None),
        (60, 0, (), None),
        (60, 30, (), None),
        (60, 60, (), None),
    ],
    ids=[
        "celery-autoretry",
        "no-time-limit",
        "zero-limit",
        "negative-limit",
        "equal-lease",
        "above-lease",
        "soft-above-hard",
        "no-soft-limit",
        "disabled-soft-limit",
        "soft-below-hard",
        "soft-equals-hard",
    ],
)
def test_route_requires_a_deliverable_worker_bounded_by_its_lease(
    hard_limit: int | None,
    soft_limit: int | None,
    autoretry_for: tuple[type[Exception], ...],
    reason: str | None,
) -> None:
    # Use real Celery tasks without registering case-specific tasks on the global app.
    with Celery("route_validation", set_as_current=False, fixups=[]) as app:
        task = app.task(autoretry_for=autoretry_for, lazy=False, shared=False)(check_owner.run)
        task.time_limit = hard_limit
        task.soft_time_limit = soft_limit
        with pytest.raises(ValueError, match=reason) if reason else nullcontext():
            replace(route, task=task)
