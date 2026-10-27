"""
One shared retained-history fixture; no adopter imports or per-adopter replay.

Run normally when dependency-selected, or directly with pytest -s --durations=10.
Counts and semantics belong to harness/due_work/tests/work_table/test_database.py. This module guards
work proportional to the small live set, using real PostgreSQL costing and buffers.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.db.models import Count, Min, Model, Q
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pytest_obligation.integrations.postgres_plans import iter_plan_nodes

from due.django.conformance.database import DatabaseWork, database_work_table, transaction_in_progress
from due.django.evidence.attempts import publish_pending_attempt_census
from due.django.models import DurableWorkState, EffectAttempt, RepairSettledBy
from due.django.public import ExecutionEligibility
from due.django.runtime import coordination
from due.django.runtime.coordination import prune_completed_work, publish_work_census

# Calibrated against both an unfiltered production census and removed indexes.
# Kept once per shared module, never multiplied by capability enrollment.
HISTORY_ROWS = 2048


@dataclass(frozen=True)
class ScanCost:
    blocks: int
    visited: float
    sequential: bool


@dataclass(frozen=True)
class QueryCase:
    """One production read set and its independently calibrated work budget."""

    name: str
    model: type[Model]
    reads: list[str]
    baseline: ScanCost
    live_rows: int
    index_name: str


def measure_reads(sql_statements: list[str], table: str) -> ScanCost:
    """Budget the operation's entire read set, counting inclusive root buffers once."""
    blocks = 0
    visited = 0.0
    sequential = False
    found_scan = False

    for sql in sql_statements:
        with connection.cursor() as cursor:
            cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql)
            root = cursor.fetchone()[0][0]["Plan"]
        print("due_work_plan=" + json.dumps({"sql": sql, "table": table, "plan": root}, sort_keys=True))
        blocks += root.get("Shared Hit Blocks", 0) + root.get("Shared Read Blocks", 0)
        for scan in iter_plan_nodes(root):
            if scan.get("Relation Name") == table:
                found_scan = True
                visited += (
                    scan["Actual Rows"]
                    + scan.get("Rows Removed by Filter", 0)
                    + scan.get("Rows Removed by Index Recheck", 0)
                ) * scan["Actual Loops"]
                sequential |= "Seq Scan" in scan["Node Type"]
    assert found_scan, f"Operation never read {table}; a no-op cannot prove a bounded query"
    return ScanCost(blocks=blocks, visited=visited, sequential=sequential)


def capture_reads(run: Callable[[], object]) -> list[str]:
    with CaptureQueriesContext(connection) as captured:
        run()
    reads: list[str] = []
    for query in captured.captured_queries:
        sql = query["sql"].lstrip()
        verb = sql.split(maxsplit=1)[0].upper()
        # These production operations use plain SELECTs and retention DELETEs.
        # Fail closed on a future CTE/other statement: silently omitting its reads
        # is unsafe, and blindly EXPLAIN ANALYZE-ing a writable CTE executes it.
        assert verb in {"SELECT", "DELETE", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"}, sql
        if verb == "SELECT":
            reads.append(sql)
    return reads


def remove_nonconstraint_indexes(table: str) -> None:
    """Remove every alternative access path, preserving actual schema constraints."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT i.indexrelid::regclass::text FROM pg_index i "
            "WHERE i.indrelid = %s::regclass AND NOT EXISTS "
            "(SELECT 1 FROM pg_constraint c WHERE c.conindid = i.indexrelid)",
            [table],
        )
        # regclass rendering already quotes schema-qualified identifiers safely.
        for (name,) in cursor.fetchall():
            cursor.execute(f"DROP INDEX {name}")


def assert_bounded_scan(cost: ScanCost, *, baseline: ScanCost, live_rows: int) -> None:
    # Relative work, not machine speed. Fourfold buffer headroom plus inspected
    # tuples rejects a full scan even if PostgreSQL serves it through an index.
    assert not cost.sequential, f"Sequential scan: {cost}"
    assert cost.visited <= live_rows * 4, f"Retained history inspected: {cost}"
    assert cost.blocks < baseline.blocks / 4, f"Insufficient buffer separation: {cost}; full history {baseline}"


def leave_dead_hot_chain() -> None:
    """What an earlier test leaves behind: the fast transactional flush DELETEs rows instead of truncating."""
    attempt = EffectAttempt.objects.create(
        work_model="tests.residue", work_id=0, claim_token=uuid4(), desired_revision=1
    )
    # No indexed or index-predicate column changes, so PostgreSQL updates the row in place (HOT).
    EffectAttempt.objects.filter(pk=attempt.pk).update(repair_settled_by=RepairSettledBy.RECEIPT)
    EffectAttempt.objects.filter(pk=attempt.pk).delete()


def populate_retained_history() -> None:
    """Populate each table once; all healthy/fault cases reuse this fixture."""
    # Start from a fresh heap, as Django's TRUNCATE flush gave. Dead HOT chains
    # newer than any open transaction on the server make CREATE INDEX set
    # indcheckxmin, which hides the equivalent index from the transaction that
    # built it and forces a sequential scan. CASCADE: receipts reference attempts.
    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE {connection.ops.quote_name(EffectAttempt._meta.db_table)} CASCADE")
    old = timezone.now() - timedelta(days=30)
    DatabaseWork.objects.bulk_create(
        [DatabaseWork(state="succeeded", completed_at=old, applied_revision=1) for _ in range(HISTORY_ROWS)]
    )
    DatabaseWork.objects.bulk_create([DatabaseWork(state="ready") for _ in range(7)])
    label = DatabaseWork._meta.label_lower
    EffectAttempt.objects.bulk_create(
        [
            EffectAttempt(
                work_model=label,
                work_id=1,
                claim_token=uuid4(),
                desired_revision=1,
                outcome="applied",
                observed_at=old,
                repair_pending=False,
            )
            for _ in range(HISTORY_ROWS)
        ]
        + [EffectAttempt(work_model=label, work_id=1, claim_token=uuid4(), desired_revision=1) for _ in range(5)]
    )
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE tests_databasework")
        cursor.execute(f"ANALYZE {connection.ops.quote_name(EffectAttempt._meta.db_table)}")


def assert_index_paths_stay_bounded(case: QueryCase) -> dict[str, int | float]:
    """Same query and budget: healthy, equivalent renamed index, then no optional indexes."""
    table = case.model._meta.db_table
    cost = measure_reads(case.reads, table)
    assert_bounded_scan(cost, baseline=case.baseline, live_rows=case.live_rows)
    costs = {"blocks": cost.blocks, "visited": cost.visited, "baseline_blocks": case.baseline.blocks}
    # An equivalent index with a different name must still pass. The
    # negative control then removes ALL optional paths, not just a name
    # that another healthy index might legitimately replace.
    replacement = next(i for i in case.model._meta.indexes if i.name == case.index_name).clone()
    replacement.name = "alternative_" + case.name
    with transaction.atomic():
        with connection.schema_editor() as editor:
            editor.add_index(case.model, replacement)
        with connection.cursor() as cursor:
            cursor.execute(f"DROP INDEX {connection.ops.quote_name(case.index_name)}")
        alternate = measure_reads(case.reads, table)
        assert_bounded_scan(alternate, baseline=case.baseline, live_rows=case.live_rows)
        costs["alternative_index_blocks"] = alternate.blocks
        remove_nonconstraint_indexes(table)
        broken = measure_reads(case.reads, table)
        with pytest.raises(AssertionError):
            assert_bounded_scan(broken, baseline=case.baseline, live_rows=case.live_rows)
        costs["without_usable_indexes_blocks"] = broken.blocks
        transaction.set_rollback(True)
    return costs


@pytest.mark.django_db(transaction=True)
def test_retained_history_does_not_drive_shared_queries() -> None:
    started = perf_counter()
    timings: dict[str, float] = {}
    # The other transaction must predate the dead chain for the chain to stay
    # recently dead while the indexes are built.
    with database_work_table(), transaction_in_progress():
        timings["table_setup"] = perf_counter() - started
        fixture_started = perf_counter()
        leave_dead_hot_chain()
        populate_retained_history()
        label = DatabaseWork._meta.label_lower
        timings["insert_and_analyze"] = perf_counter() - fixture_started
        assertions_started = perf_counter()
        work_table = DatabaseWork._meta.db_table
        attempt_table = EffectAttempt._meta.db_table
        full_work = capture_reads(
            lambda: list(DatabaseWork.objects.values("state").annotate(count=Count("id"), oldest=Min("created_at"))),
        )
        full_attempts = capture_reads(
            lambda: list(EffectAttempt.objects.filter(work_model=label).values("outcome").annotate(count=Count("pk"))),
        )
        work_baseline = measure_reads(full_work, work_table)
        attempt_baseline = measure_reads(full_attempts, attempt_table)
        census = capture_reads(lambda: publish_work_census(DatabaseWork))
        gated_census = capture_reads(
            lambda: publish_work_census(DatabaseWork, eligibility=ExecutionEligibility(lambda now: Q(rearm_count=0)))
        )
        # State census reads the live set once; splitting time-due work into
        # eligible/waiting needs one additional pass, not an ID subquery that
        # visits that same set twice. Reuse this root history fixture and count
        # actual PostgreSQL visits across the complete production read set.
        census_cost = measure_reads(census, work_table)
        gated_cost = measure_reads(gated_census, work_table)
        assert gated_cost.visited <= 2 * census_cost.visited, (census_cost, gated_cost)
        pending = capture_reads(lambda: publish_pending_attempt_census(DatabaseWork))
        # EXPLAIN only the captured SELECT, never the DELETE. The query must
        # come from production retention, including its ordering and LIMIT.
        with transaction.atomic():
            retention = capture_reads(
                lambda: prune_completed_work(DatabaseWork, completed_before=timezone.now(), batch_size=3),
            )
            transaction.set_rollback(True)
        cases = [
            QueryCase(
                name="census",
                model=DatabaseWork,
                reads=census,
                baseline=work_baseline,
                live_rows=7,
                index_name="db_proof_census_ix",
            ),
            QueryCase(
                name="retention",
                model=DatabaseWork,
                reads=retention,
                baseline=work_baseline,
                live_rows=3,
                index_name="db_proof_prune_ix",
            ),
            QueryCase(
                name="pending",
                model=EffectAttempt,
                reads=pending,
                baseline=attempt_baseline,
                live_rows=5,
                index_name="effect_attempt_int_repair_ix",
            ),
        ]
        costs = {case.name: assert_index_paths_stay_bounded(case) for case in cases}
        # The fault deliberately introduces a success metric label; suppress
        # metric writes so that injected defect cannot pollute later tests.
        with (
            patch.object(coordination, "ATTENTION_WORK_STATES", tuple(DurableWorkState.values)),
            patch.object(coordination, "record_work_census"),
        ):
            unfiltered = capture_reads(lambda: publish_work_census(DatabaseWork))
        with pytest.raises(AssertionError):
            assert_bounded_scan(measure_reads(unfiltered, work_table), baseline=work_baseline, live_rows=7)

        # A second full-history read used to escape the GROUP BY substring
        # selector. Conversely, an additional cheap indexed read is legitimate:
        # the contract bounds total work, not an arbitrary query count of one.
        def census_with_extra_read(*, full_history: bool) -> None:
            publish_work_census(DatabaseWork)
            query = DatabaseWork.objects.all() if full_history else DatabaseWork.objects.filter(state="ready")
            query.count()

        extra = capture_reads(lambda: census_with_extra_read(full_history=True))
        with pytest.raises(AssertionError):
            assert_bounded_scan(measure_reads(extra, work_table), baseline=work_baseline, live_rows=7)
        bounded = capture_reads(lambda: census_with_extra_read(full_history=False))
        assert_bounded_scan(measure_reads(bounded, work_table), baseline=work_baseline, live_rows=7)
        timings["assertions_and_negative_controls"] = perf_counter() - assertions_started
        cleanup_started = perf_counter()
        EffectAttempt.objects.filter(work_model=label).delete()
    timings["cleanup"] = perf_counter() - cleanup_started
    timings["test_total"] = perf_counter() - started
    print(
        "due_work_scale="
        + json.dumps({"history_rows_per_table": HISTORY_ROWS, "seconds": timings, "costs": costs}, sort_keys=True)
    )
