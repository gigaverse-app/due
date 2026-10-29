"""
A database-only transition may take its claim inside its own transaction.

The fused claim saves the claim's write and commit. What it gives up is the
attempt count of a process that dies mid-transition, so AUTO fuses only an
on-time attempt: a rollback never moves availability, so the attempt after a
death is late and takes a committed, counted claim.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from typing import ClassVar

import pytest
from django.db import close_old_connections, connection
from django.test.utils import CaptureQueriesContext
from pytest_obligation.worker_death import WorkerDied

from due.django.public import (
    ClaimFusion,
    ContinueAfter,
    DatabaseEffect,
    DurableWorkState,
    ReplaySafeEffect,
    database_now,
)
from due.django.testing import use_database_clock
from tests.harness.primitive import PrimitiveOwner, PrimitiveWork, primitive, record
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


class Advance(DatabaseEffect[PrimitiveOwner, PrimitiveWork]):
    """Appends to the owner; finishes at eight characters. ``fault`` fails or kills the next transition."""

    fault: ClassVar[BaseException | None] = None
    entered: ClassVar[list[int]] = []

    def transition(self, work: PrimitiveWork) -> ContinueAfter | None:
        type(self).entered.append(work.pk)
        owner = PrimitiveOwner.objects.get(pk=work.owner_id)
        owner.value += "+"
        owner.save(update_fields=["value"])
        if type(self).fault is not None:
            raise type(self).fault
        return ContinueAfter(timedelta(seconds=1), until=None) if len(owner.value) < 8 else None


def capability(fusion: ClaimFusion) -> Advance:
    effect = type(f"Advance{fusion.value.title()}", (Advance,), {"claim_fusion": fusion})
    return effect.for_owner(
        owner=PrimitiveOwner, work=PrimitiveWork, input_fields=("universe", "payload"), policy=primitive.policy
    )


@pytest.fixture(autouse=True)
def reset_faults():
    Advance.fault = None
    Advance.entered = []
    yield
    Advance.fault = None


def on_time(work: PrimitiveWork) -> PrimitiveWork:
    """Due exactly now, as a deadline hint delivers it."""
    PrimitiveWork.objects.filter(pk=work.pk).update(available_at=database_now())
    work.refresh_from_db()
    return work


def owner_value(work: PrimitiveWork) -> str:
    return PrimitiveOwner.objects.get(pk=work.owner_id).value


@pytest.mark.parametrize("fusion", [ClaimFusion.AUTO, ClaimFusion.ALWAYS])
@pytest.mark.parametrize("finishes", [False, True], ids=["continues", "completes"])
def test_an_on_time_attempt_claims_transitions_and_settles_in_one_commit(fusion, finishes):
    effect = capability(fusion)
    work = on_time(record())
    if finishes:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="before+")
    before = (work.attempt_count, work.claim_generation, work.fencing_token)
    with CaptureQueriesContext(connection) as queries:
        assert effect.attempt_execution(work.pk)
    assert [q["sql"] for q in queries if q["sql"] in ("COMMIT", "ROLLBACK")] == ["COMMIT"]
    work.refresh_from_db()
    # The settlement write recorded the claim the ordinary path would have committed first.
    assert work.attempt_count == before[0] + 1
    assert work.claim_generation == before[1] + 1
    assert work.fencing_token != before[2]
    assert work.state == (DurableWorkState.SUCCEEDED if finishes else DurableWorkState.READY)
    assert work.lease_expires_at is None


@pytest.mark.parametrize("fusion", [ClaimFusion.AUTO, ClaimFusion.ALWAYS])
def test_a_failed_fused_transition_rolls_back_and_is_counted_once(fusion):
    effect = capability(fusion)
    work = on_time(record())
    Advance.fault = OSError("product write failed")
    with pytest.raises(OSError, match="product write failed"):
        effect.attempt_execution(work.pk)
    work.refresh_from_db()
    # The transition ran once; its product write rolled back; the retry is recorded as ordinarily.
    assert Advance.entered == [work.pk]
    assert owner_value(work) == "before"
    assert (work.state, work.attempt_count, work.rearm_count) == (DurableWorkState.RETRYABLE, 1, 1)


def test_auto_loses_at_most_the_on_time_death_then_counts_every_attempt():
    effect = capability(ClaimFusion.AUTO)
    work = on_time(record())
    due = work.available_at
    Advance.fault = WorkerDied()

    # On time: fused, so the death leaves no trace, and availability does not move.
    with use_database_clock(lambda: due), pytest.raises(WorkerDied):
        effect.attempt_execution(work.pk)
    work.refresh_from_db()
    assert (work.state, work.attempt_count, work.available_at) == (DurableWorkState.READY, 0, due)

    # Any later attempt is late: its claim commits and counts before the transition can kill it.
    with use_database_clock(lambda: due + effect.claim_fusion_window * 2), pytest.raises(WorkerDied):
        effect.attempt_execution(work.pk)
    work.refresh_from_db()
    assert (work.state, work.attempt_count) == (DurableWorkState.CLAIMED, 1)
    assert owner_value(work) == "before"


def test_always_trades_the_attempt_count_of_every_death():
    effect = capability(ClaimFusion.ALWAYS)
    work = on_time(record())
    Advance.fault = WorkerDied()
    for lateness in (0, 10, 100):
        with use_database_clock(lambda: work.available_at + timedelta(seconds=lateness)), pytest.raises(WorkerDied):
            effect.attempt_execution(work.pk)
    work.refresh_from_db()
    assert (work.state, work.attempt_count, len(Advance.entered)) == (DurableWorkState.READY, 0, 3)


def test_concurrent_fused_attempts_run_the_transition_once():
    effect = capability(ClaimFusion.AUTO)
    work = on_time(record())
    inside, release = Event(), Event()

    original = Advance.transition

    def held(self, current):
        inside.set()
        assert release.wait(10)
        return original(self, current)

    def attempt(_: int) -> bool:
        close_old_connections()
        try:
            return effect.attempt_execution(work.pk)
        finally:
            close_old_connections()

    Advance.transition = held  # type: ignore[method-assign]
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(attempt, 0)
            assert inside.wait(10)
            Advance.transition = original  # type: ignore[method-assign]
            second = pool.submit(attempt, 1)
            release.set()
            outcomes = sorted([first.result(10), second.result(10)])
    finally:
        Advance.transition = original  # type: ignore[method-assign]
    # The second attempt waited on the owner lock, then found the row released for later.
    assert outcomes == [False, True]
    assert owner_value(work) == "before+"


def test_fusion_is_only_for_database_transitions_with_a_window_inside_the_lease():
    class Remote(ReplaySafeEffect[PrimitiveOwner, PrimitiveWork]):
        claim_fusion = ClaimFusion.AUTO

        def perform_effect(self, work):
            return None

    with pytest.raises(ValueError, match="only database-only effects"):
        Remote.for_owner(
            owner=PrimitiveOwner, work=PrimitiveWork, input_fields=("universe", "payload"), policy=primitive.policy
        )
    wide = type("Wide", (Advance,), {"claim_fusion": ClaimFusion.AUTO, "claim_fusion_window": timedelta(days=1)})
    with pytest.raises(ValueError, match="claim_fusion_window"):
        wide.for_owner(
            owner=PrimitiveOwner, work=PrimitiveWork, input_fields=("universe", "payload"), policy=primitive.policy
        )
