"""
Work a tick makes due before its dispatch phase is dispatched by that same tick.

Reap, census and retention judge against the tick's one clock read
(``RecoveryPass.clock``); dispatch reads a clock of its own. Discovery stamps
new work's ``available_at`` from a read taken after the tick's, so a dispatch
selecting at the tick's instant would leave that work for the next tick, one
whole cadence late. A reaped reservation is stamped at the reap's instant,
which dispatch must not select before either.

The coordination clock here advances one second per read, so "a later read"
never depends on the database clock's resolution.

Design: docs/architecture.md.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from due.django.public import AppliedEvidenceContract, Discovery, DurableWorkState
from due.django.runtime.coordination import claim_work, database_now
from due.django.testing import expire_lease, use_database_clock
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


def _advancing_clock() -> Callable[[], datetime]:
    """The coordination clock, one second later on every read."""
    reads = 0
    origin = database_now()

    def now() -> datetime:
        nonlocal reads
        reads += 1
        return origin + timedelta(seconds=reads)

    return now


def _discovering(owners: list[PrimitiveOwner]):
    """The primitive with a discovery phase: it pages first, so the tick's clock is read before anything else."""
    return replace(
        primitive,
        discovery=Discovery(
            unbounded_because="Only explicitly selected synthetic fixture owners are candidates.",
            candidates=lambda: PrimitiveOwner.objects.filter(
                pk__in=[owner.pk for owner in owners], primitivework__isnull=True
            ).order_by("pk"),
            desired_for=lambda owner, existing: PrimitiveWork(payload="derived", universe="acme"),
            page_size=10,
            applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
        ),
    )


def test_work_discovered_in_a_tick_is_dispatched_in_that_tick():
    owner = PrimitiveOwner.objects.create()
    capability = _discovering([owner])

    with use_database_clock(_advancing_clock()):
        assert capability.recover() == 1

    work = PrimitiveWork.objects.get(owner=owner)
    assert work.state == DurableWorkState.QUEUED
    assert PrimitiveEffect.deliveries == [(work.pk, work.fencing_token)]


def test_a_reservation_reaped_in_a_tick_is_dispatched_in_that_tick():
    work = record()
    assert claim_work(PrimitiveWork, work.pk, lease_duration=primitive.policy.lease_duration, queued=True)
    expire_lease(work)
    # Discovery runs first and finds nothing, but it reads the tick's clock before the reap does.
    capability = _discovering([])

    with use_database_clock(_advancing_clock()):
        assert capability.recover() == 1

    work.refresh_from_db()
    assert work.state == DurableWorkState.QUEUED
    # One delivery: the lost reservation was never published here, only its re-dispatch.
    assert PrimitiveEffect.deliveries == [(work.pk, work.fencing_token)]
