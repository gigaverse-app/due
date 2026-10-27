"""
Database round trips per recovery tick, claim and execution of a work-table capability.

Every statement is a round trip, and idle ticks dominate: each work-table
effect runs one per cadence whether or not anything is owed. A tick reads the
coordination clock once (``RecoveryPass.clock``) for its holds, reap, census and
retention; only dispatch, which must see work earlier phases made due, and the
heartbeat read their own. A claim returns it with its row. A reaped claim locks its owner
by id in the same statement that names it, with no snapshot read first.

The budgets are exact, so a returning redundant read fails here rather than
showing up as database load. Per-row costs are the difference between two
batch sizes, so any extra per-row statement fails, not only the ones removed.

Design: docs/architecture.md.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest
from django.db import connection
from django.db.models import F
from django.test.utils import CaptureQueriesContext

from due.django.models import RecoveryDeferral
from due.django.public import AppliedEvidenceContract, Discovery, DurableWorkState
from due.django.runtime.coordination import claim_work, database_now
from due.django.testing import expire_lease
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

CLOCK = "clock"
HOLDS = "read holds"
HOLD_CENSUS = "count holds"
READ_WORK = "read work"
COUNT_WORK = "count work"
LOCK_WORK = "lock work"
UPDATE_WORK = "update work"
DELETE_WORK = "delete work"
READ_OWNER = "read owner"
COUNT_OWNER = "count owner"
LOCK_OWNER = "lock owner"
UPDATE_OWNER = "update owner"

#: An idle tick: reap pages (reading the tick's clock and its holds once),
#: dispatch pages at a clock of its own, the census groups the attention
#: states, retention looks for expired successes, the tick publishes its holds
#: and, being clean, stamps its heartbeat at a third clock read.
IDLE_TICK = [CLOCK, HOLDS, READ_WORK, CLOCK, READ_WORK, COUNT_WORK, READ_WORK, HOLD_CENSUS, CLOCK]
#: A claim: one locked selection returns the availability/lease clock, then write.
CLAIM = [LOCK_WORK, UPDATE_WORK]


def _shape(sql: str) -> str:
    """What a statement does, by the table it targets: enough to name every round trip of these paths."""
    work, owner, holds = (model._meta.db_table for model in (PrimitiveWork, PrimitiveOwner, RecoveryDeferral))
    if sql == "SELECT clock_timestamp()":
        return CLOCK
    for verb, table, shape in (
        ("UPDATE", work, UPDATE_WORK),
        ("UPDATE", owner, UPDATE_OWNER),
        ("DELETE FROM", work, DELETE_WORK),
    ):
        if sql.startswith(f'{verb} "{table}"'):
            return shape
    # The outermost FROM: an owner lock names the work table only in its sub-select.
    target = sql.split(" FROM ", 1)[1].split()[0].strip('"') if sql.startswith("SELECT ") else ""
    counted = "GROUP BY" in sql or "COUNT(" in sql
    if target == holds:
        return HOLD_CENSUS if counted else HOLDS
    if target == work:
        return LOCK_WORK if " FOR UPDATE" in sql else COUNT_WORK if counted else READ_WORK
    if target == owner:
        return LOCK_OWNER if " FOR UPDATE" in sql else COUNT_OWNER if counted else READ_OWNER
    raise AssertionError(f"unclassified statement: {sql}")


def _round_trips(run: Callable[[], object]) -> list[str]:
    """Every statement ``run`` sends, in order; transaction control is the same before and after and left out."""
    with CaptureQueriesContext(connection) as captured:
        run()
    return [_shape(query["sql"]) for query in captured.captured_queries if query["sql"] not in ("BEGIN", "COMMIT")]


def _per_row(seed: Callable[[int], object], small: int = 1, large: int = 3) -> Counter[str]:
    """The round trips one more row costs a tick: (large batch - small batch) / (large - small), exactly."""
    costs = []
    for size in (small, large):
        PrimitiveWork.objects.all().delete()
        seed(size)
        costs.append(Counter(_round_trips(primitive.recover)))
    extra = costs[1] - costs[0]
    rows = large - small
    assert costs[0] - costs[1] == Counter(), "a larger batch must not skip a statement the smaller one issues"
    assert all(count % rows == 0 for count in extra.values()), extra
    return Counter({shape: count // rows for shape, count in extra.items()})


def _due(size: int) -> None:
    for _ in range(size):
        record()


def _expired(state: DurableWorkState) -> Callable[[int], None]:
    def seed(size: int) -> None:
        for _ in range(size):
            work = record()
            lease = primitive.policy.lease_duration
            assert claim_work(PrimitiveWork, work.pk, lease_duration=lease, queued=state == DurableWorkState.QUEUED)
            expire_lease(work)

    return seed


def test_an_idle_tick_reads_the_clock_once_for_every_phase_but_dispatch():
    assert _round_trips(primitive.recover) == IDLE_TICK


def test_an_idle_discovery_tick_reads_the_clock_once_for_every_phase_but_dispatch():
    capability = replace(
        primitive,
        discovery=Discovery(
            unbounded_because="Only explicitly selected synthetic fixture owners are candidates.",
            candidates=lambda: PrimitiveOwner.objects.filter(value="discoverable").order_by("pk"),
            desired_for=lambda owner, existing: None,
            page_size=10,
            applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
        ),
    )
    # Discovery pages first, so its page reads the tick's clock and holds; the census counts its candidates.
    assert _round_trips(capability.recover) == [
        CLOCK,
        HOLDS,
        READ_OWNER,
        *IDLE_TICK[2:5],
        COUNT_OWNER,
        *IDLE_TICK[5:],
    ]


def test_each_due_row_costs_one_claim():
    assert _per_row(_due) == Counter(CLAIM)


@pytest.mark.parametrize("state", [DurableWorkState.QUEUED, DurableWorkState.CLAIMED], ids=["reserved", "running"])
def test_each_expired_lease_costs_its_reap_and_one_claim(state: DurableWorkState):
    # Reap: lock the owner by id (no snapshot read of the row first), lock the row, write it. Then re-dispatch.
    assert _per_row(_expired(state)) == Counter([LOCK_OWNER, LOCK_WORK, UPDATE_WORK, *CLAIM])


def test_retention_deletes_without_a_clock_read_of_its_own():
    retention = primitive.policy.retention
    assert retention is not None, "the primitive retains successes for a bounded time"
    for _ in range(3):
        record()
    PrimitiveWork.objects.update(
        state=DurableWorkState.SUCCEEDED,
        applied_revision=F("desired_revision"),
        completed_at=database_now() - retention - timedelta(days=1),
    )

    # One DELETE after the retention page; its cut-off is the tick's clock.
    assert _round_trips(primitive.recover) == [*IDLE_TICK[:7], DELETE_WORK, *IDLE_TICK[7:]]
    assert not PrimitiveWork.objects.exists()


def test_a_fast_path_claim_returns_the_clock_with_its_row():
    work = record()
    assert _round_trips(lambda: primitive.enqueue_execution(work.pk)) == CLAIM
    assert PrimitiveEffect.deliveries == [(work.pk, PrimitiveWork.objects.get(pk=work.pk).fencing_token)]


def test_a_worker_without_a_reservation_claims_runs_and_settles():
    work = record()
    assert _round_trips(lambda: primitive.attempt_execution(work.pk)) == [
        *CLAIM,
        # The provider guard renews with the UPDATE's database timestamp.
        UPDATE_WORK,
        # Settlement: owner before work, then the fenced write and the product callback.
        LOCK_OWNER,
        LOCK_WORK,
        UPDATE_WORK,
        UPDATE_OWNER,
    ]
