"""Small correctness and schema controls; large-history proofs live separately."""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import OperationalError, connection, transaction
from django.db.models import IntegerField, Max, QuerySet, Value
from django.utils import timezone
from prometheus_client import REGISTRY

from due.django.conformance.database import DatabaseWork, database_work_table
from due.django.conformance.indexes import assert_work_indexes
from due.django.conformance.worker_connection import database_worker_connection
from due.django.evidence.attempts import publish_pending_attempt_census
from due.django.models import (
    DurableWorkState,
    EffectAttempt,
    EffectAttemptOutcome,
)
from due.django.runtime import coordination, metrics
from due.django.runtime.coordination import prune_completed_work, publish_work_census


@pytest.fixture
def work_table(transactional_db: None) -> Iterator[None]:
    with database_work_table():
        yield


def _assert_census_counts_ages_and_resets() -> None:
    now = timezone.now()
    label = DatabaseWork._meta.label_lower
    # Independent expectations: deriving these from ATTENTION_WORK_STATES would
    # let an omitted failed state disappear from both production and the test.
    counts = {
        "ready": 3,
        "queued": 2,
        "claimed": 1,
        "retryable": 4,
        "unknown": 2,
        "terminal_failed": 2,
        "exhausted": 1,
    }
    for state, count in counts.items():
        metrics.record_work_census(label, DurableWorkState(state), count=-1, oldest_age_seconds=-1)
        for position in range(count):
            row = DatabaseWork.objects.create(
                state=state,
                universe="tests",
                available_at=now + timedelta(days=1),
                lease_expires_at=now if state in ("queued", "claimed") else None,
                completed_at=now if state in ("terminal_failed", "exhausted") else None,
            )
            age = timedelta(hours=2) if position == 0 else timedelta(minutes=30)
            if state == "claimed":
                age = -timedelta(minutes=5)  # Future timestamps must not produce negative ages.
            DatabaseWork.objects.filter(pk=row.pk).update(created_at=now - age)
    DatabaseWork.objects.create(state="succeeded", completed_at=now, applied_revision=1)
    publish_work_census(DatabaseWork, now=lambda: now, undiscovered=7)
    assert REGISTRY.get_sample_value("due_outstanding", {"effect": label}) == 19
    for state, count in counts.items():
        labels = {"effect": label, "state": state}
        assert REGISTRY.get_sample_value("due_backlog", labels) == count, "wrong state count"
        assert REGISTRY.get_sample_value("due_oldest_age_seconds", labels) == (0 if state == "claimed" else 7200), (
            "wrong oldest age"
        )
    assert REGISTRY.get_sample_value("due_backlog", {"effect": label, "state": "succeeded"}) is None
    DatabaseWork.objects.exclude(state="succeeded").delete()
    publish_work_census(DatabaseWork, now=lambda: now)
    assert REGISTRY.get_sample_value("due_outstanding", {"effect": label}) == 0
    for state in counts:
        labels = {"effect": label, "state": state}
        assert REGISTRY.get_sample_value("due_backlog", labels) == 0
        assert REGISTRY.get_sample_value("due_oldest_age_seconds", labels) == 0


def test_census_counts_delayed_work_and_failures_then_resets(work_table: None) -> None:
    _assert_census_counts_ages_and_resets()


@pytest.mark.parametrize("fault", ["newest_age", "constant_count", "omit_failed_state"])
def test_census_proof_rejects_incorrect_aggregation(
    work_table: None, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    if fault == "newest_age":
        monkeypatch.setattr(coordination, "Min", Max)
    elif fault == "constant_count":
        monkeypatch.setattr(coordination, "Count", lambda field: Value(1, output_field=IntegerField()))
    else:
        monkeypatch.setattr(
            coordination,
            "ATTENTION_WORK_STATES",
            tuple(s for s in coordination.ATTENTION_WORK_STATES if s != "terminal_failed"),
        )
    with pytest.raises(AssertionError):
        _assert_census_counts_ages_and_resets()


def _assert_retention_pages() -> None:
    now = timezone.now()
    old = now - timedelta(days=30)
    # Lowest IDs are deliberately newer; equal oldest dates also pin the ID tie-break.
    successes = [
        DatabaseWork.objects.create(state="succeeded", completed_at=old + timedelta(days=days), applied_revision=1)
        for days in (2, 0, 0, 1)
    ]
    recent = DatabaseWork.objects.create(state="succeeded", completed_at=now, applied_revision=1)
    owed = DatabaseWork.objects.create(state="unknown")
    failed = DatabaseWork.objects.create(state="exhausted", completed_at=old)
    assert prune_completed_work(DatabaseWork, completed_before=now, batch_size=1) == 1
    assert set(DatabaseWork.objects.values_list("pk", flat=True)) == {
        successes[0].pk,
        successes[2].pk,
        successes[3].pk,
        recent.pk,
        owed.pk,
        failed.pk,
    }, "retention selected the wrong oldest row or ID tie-break"
    assert prune_completed_work(DatabaseWork, completed_before=now, batch_size=1) == 1
    assert set(DatabaseWork.objects.values_list("pk", flat=True)) == {
        successes[0].pk,
        successes[3].pk,
        recent.pk,
        owed.pk,
        failed.pk,
    }
    assert prune_completed_work(DatabaseWork, completed_before=now, batch_size=1) == 1
    assert DatabaseWork.objects.filter(pk=successes[0].pk).exists()
    assert not DatabaseWork.objects.filter(pk=successes[3].pk).exists()
    assert prune_completed_work(DatabaseWork, completed_before=now, batch_size=2) == 1
    assert prune_completed_work(DatabaseWork, completed_before=now, batch_size=2) == 0


@pytest.mark.parametrize("fault", [None, "id_only", "reverse_tie"])
def test_retention_is_ordered_bounded_and_preserves_owed_and_failed_work(work_table: None, fault: str | None) -> None:
    original_order_by = QuerySet.order_by

    def incorrect_order(self: QuerySet[DatabaseWork], *fields: str) -> QuerySet[DatabaseWork]:
        if self.model is DatabaseWork and fields == ("completed_at", "id"):
            fields = ("id",) if fault == "id_only" else ("completed_at", "-id")
        return original_order_by(self, *fields)

    if fault:
        with (
            patch.object(QuerySet, "order_by", incorrect_order),
            pytest.raises(AssertionError, match="wrong oldest row"),
        ):
            _assert_retention_pages()
    else:
        _assert_retention_pages()


def test_pending_attempt_census_is_scoped_and_resets(work_table: None) -> None:
    label = DatabaseWork._meta.label_lower
    for model, pending, outcome in [
        (label, True, "unknown"),
        (label, True, "unknown"),
        (label, True, "applied"),
        (label, False, "applied"),
        ("other.capability", True, "not_applied"),
    ]:
        EffectAttempt.objects.create(
            work_model=model,
            work_id=1,
            claim_token=uuid4(),
            desired_revision=1,
            outcome=outcome,
            repair_pending=pending,
            observed_at=None if outcome == "unknown" else timezone.now(),
        )
    publish_pending_attempt_census(DatabaseWork)
    for outcome, count in [("unknown", 2), ("applied", 1), ("not_applied", 0)]:
        assert (
            REGISTRY.get_sample_value("due_pending_provider_attempts", {"effect": label, "outcome": outcome}) == count
        )
    EffectAttempt.objects.filter(work_model=label, repair_pending=True).delete()
    publish_pending_attempt_census(DatabaseWork)
    for outcome in EffectAttemptOutcome:
        assert REGISTRY.get_sample_value("due_pending_provider_attempts", {"effect": label, "outcome": outcome}) == 0


@pytest.mark.parametrize("defect", ["missing", "key_order", "include", "predicate", "unfiltered"])
def test_schema_guard_rejects_migrated_index_drift(work_table: None, defect: str) -> None:
    assert_work_indexes(DatabaseWork)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("DROP INDEX db_proof_census_ix")
            if defect != "missing":
                keys = "created_at, state" if defect == "key_order" else "state, created_at"
                include = "" if defect == "include" else " INCLUDE (id)"
                predicate = (
                    ""
                    if defect == "unfiltered"
                    else " WHERE state IN ('ready')"
                    if defect == "predicate"
                    else (
                        " WHERE state IN ('ready', 'queued', 'claimed', 'retryable', 'unknown', 'terminal_failed', 'exhausted')"
                    )
                )
                cursor.execute(f"CREATE INDEX mutated_census ON tests_databasework ({keys}){include}{predicate}")
        with pytest.raises(AssertionError, match="contract_census_ix"):
            assert_work_indexes(DatabaseWork)
        transaction.set_rollback(True)
    assert_work_indexes(DatabaseWork)


def test_schema_guard_rejects_an_index_left_invalid_by_a_failed_build(work_table: None) -> None:
    # A failed concurrent build leaves a real catalog entry. Its definition
    # looks usable, but PostgreSQL will not use it until the build is repaired.
    from django.db import IntegrityError

    DatabaseWork.objects.bulk_create([DatabaseWork(), DatabaseWork()])
    DatabaseWork.objects.update(created_at=timezone.now())
    with connection.cursor() as cursor:
        cursor.execute("DROP INDEX db_proof_census_ix")
        with pytest.raises(IntegrityError):
            cursor.execute(
                "CREATE UNIQUE INDEX CONCURRENTLY invalid_census ON tests_databasework (state, created_at) "
                "INCLUDE (id) WHERE state IN ('ready', 'queued', 'claimed', 'retryable', 'unknown', 'terminal_failed', 'exhausted')"
            )
    with pytest.raises(AssertionError, match="contract_census_ix"):
        assert_work_indexes(DatabaseWork)


def test_schema_guard_rejects_sample_equivalent_but_unusable_predicate(work_table: None) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DROP INDEX db_proof_census_ix")
        cursor.execute(
            "CREATE INDEX misleading_census ON tests_databasework (state, created_at) INCLUDE (id) "
            "WHERE lower(state) IN ('ready', 'queued', 'claimed', 'retryable', 'unknown', 'terminal_failed', 'exhausted')"
        )
    with pytest.raises(AssertionError, match="index|scan"):
        assert_work_indexes(DatabaseWork)


@pytest.mark.parametrize(
    "predicate",
    [
        "state = ANY (ARRAY['exhausted', 'terminal_failed', 'unknown', 'retryable', 'claimed', 'queued', 'ready'])",
        "state = 'ready' OR state = 'queued' OR state = 'claimed' OR state = 'retryable' OR state = 'unknown' OR state = 'terminal_failed' OR state = 'exhausted'",
    ],
)
def test_schema_guard_accepts_renamed_equivalent_indexes(work_table: None, predicate: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DROP INDEX db_proof_census_ix")
        cursor.execute(
            "CREATE INDEX equivalent_census ON tests_databasework (state, created_at) INCLUDE (id) WHERE " + predicate
        )
    assert_work_indexes(DatabaseWork)


def _assert_retention_rechecks_after_selection(
    *,
    completion_delay: timedelta | None = None,
    lock_before_delete: bool = False,
) -> None:
    now = timezone.now()
    selected = DatabaseWork.objects.create(state="succeeded", completed_at=now - timedelta(days=30), applied_revision=1)
    settled = DatabaseWork.objects.create(state="succeeded", completed_at=now - timedelta(days=29), applied_revision=1)
    reopened = False

    def reopen() -> None:
        with (
            database_worker_connection(statement_timeout_ms=100 if lock_before_delete else 5_000),
            transaction.atomic(),
        ):
            current = DatabaseWork.objects.select_for_update().get(pk=selected.pk)
            coordination.reopen_locked_work(current, available_at=now, reason="concurrent_repair")
            if completion_delay is not None:
                # The same identity can finish its repair before retention's
                # DELETE. State is succeeded again, but its new completion age
                # must get a fresh retention window. Use production fencing.
                claim = coordination.claim_work(
                    DatabaseWork, current.pk, lease_duration=timedelta(minutes=1), now=lambda: now
                )
                assert claim is not None
                assert coordination.complete_work(claim, now=lambda: now + completion_delay)

    with ThreadPoolExecutor(max_workers=1) as pool:
        # Django's execute-wrapper is the scheduling seam only; both actors
        # still run production transitions and real SQL on separate connections.
        def before_delete(
            execute: Callable[[str, Any, bool, dict[str, Any]], Any],
            sql: str,
            params: Any,
            many: bool,
            context: dict[str, Any],
        ) -> Any:
            nonlocal reopened
            if not reopened and sql.lstrip().startswith("DELETE") and DatabaseWork._meta.db_table in sql:
                reopened = True
                pool.submit(reopen).result(timeout=10)
            return execute(sql, params, many, context)

        with transaction.atomic(), connection.execute_wrapper(before_delete):
            if lock_before_delete:
                # Fault control: a row lock makes the competing worker wait for
                # this connection. A Future timeout alone cannot stop its SQL.
                DatabaseWork.objects.select_for_update().get(pk=selected.pk)
            pruned = prune_completed_work(DatabaseWork, completed_before=now, batch_size=2)
    assert reopened, "retention must reach the contested deletion"
    assert pruned == 1, "work with changed retention eligibility was pruned"
    selected.refresh_from_db()
    if completion_delay is None:
        assert selected.state == DurableWorkState.READY
        assert selected.completed_at is None
    else:
        assert selected.state == DurableWorkState.SUCCEEDED
        assert selected.completed_at == now + completion_delay
    assert not DatabaseWork.objects.filter(pk=settled.pk).exists()


@pytest.mark.parametrize(
    ("completion_delay", "remove_recheck"),
    [
        (None, None),
        (None, "both"),
        (timedelta(0), None),
        (timedelta(0), "cutoff"),
        (timedelta(0), "inclusive_cutoff"),
        (timedelta(seconds=1), None),
        (timedelta(seconds=1), "cutoff"),
    ],
    ids=[
        "reopened",
        "rejects-no-recheck",
        "completed-at-cutoff",
        "rejects-no-cutoff-at-boundary",
        "rejects-inclusive-cutoff",
        "completed-after-cutoff",
        "rejects-no-cutoff-after-boundary",
    ],
)
def test_retention_rechecks_eligibility_after_a_competing_reopen(
    work_table: None,
    completion_delay: timedelta | None,
    remove_recheck: str | None,
) -> None:
    original_filter = QuerySet.filter

    def filter_without_recheck(self: QuerySet[DatabaseWork], *args: Any, **kwargs: Any) -> QuerySet[DatabaseWork]:
        if self.model is DatabaseWork and "id__in" in kwargs:
            if remove_recheck == "both":
                kwargs.pop("state", None)
            cutoff = kwargs.pop("completed_at__lt")
            if remove_recheck == "inclusive_cutoff":
                kwargs["completed_at__lte"] = cutoff
        return original_filter(self, *args, **kwargs)

    if remove_recheck:
        with (
            patch.object(QuerySet, "filter", filter_without_recheck),
            pytest.raises(AssertionError, match="changed retention eligibility"),
        ):
            _assert_retention_rechecks_after_selection(completion_delay=completion_delay)
    else:
        _assert_retention_rechecks_after_selection(completion_delay=completion_delay)


@pytest.mark.parametrize("previous", ["on", "off"])
def test_schema_eligibility_probe_restores_the_callers_planner_setting(work_table: None, previous: str) -> None:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('enable_seqscan', %s, true)", [previous])
        assert_work_indexes(DatabaseWork)
        cursor.execute("SHOW enable_seqscan")
        assert cursor.fetchone()[0] == previous
        transaction.set_rollback(True)


def test_retention_lock_conflict_cannot_strand_the_executor(work_table: None) -> None:
    with pytest.raises(OperationalError, match="statement timeout"):
        _assert_retention_rechecks_after_selection(lock_before_delete=True)
    # Error cleanup must release the lock, close the worker's connection and
    # leave the main connection usable; ThreadPoolExecutor must have joined.
    assert list(DatabaseWork.objects.order_by("id").values_list("state", flat=True)) == ["succeeded", "succeeded"]


@pytest.mark.parametrize("equivalent", [True, False], ids=["same-predicate", "broader-predicate"])
def test_schema_guard_checks_alternate_bitmap_index_predicate(work_table: None, equivalent: bool) -> None:
    from pytest_obligation.integrations.django import selection

    predicate = "state IN ('ready', 'retryable')" if equivalent else "state IN ('ready', 'retryable', 'succeeded')"
    with connection.cursor() as cursor:
        cursor.execute("CREATE INDEX alternative_due ON tests_databasework (available_at, id) WHERE " + predicate)
    explain = selection.explain_index_eligibility

    def alternate_plan(queryset: QuerySet[DatabaseWork], *, bitmap_only: bool = False) -> dict[str, Any]:
        if queryset.query.order_by != ("state", "available_at", "id"):
            return explain(queryset, bitmap_only=bitmap_only)
        # Reproduce an observed PostgreSQL bitmap plan deterministically: the
        # heap rechecks the due predicate after a bitmap of an alternate index.
        # Its catalog and the guard are real; planner cost tie-breaking is not
        # what this control tests. The broader index must still be rejected.
        return {
            "Node Type": "Bitmap Heap Scan",
            "Relation Name": DatabaseWork._meta.db_table,
            "Recheck Cond": "state IN ('ready', 'retryable')",
            "Plans": [{"Node Type": "Bitmap Index Scan", "Index Name": "alternative_due"}],
        }

    with patch.object(selection, "explain_index_eligibility", alternate_plan):
        if equivalent:
            assert_work_indexes(DatabaseWork)
        else:
            with pytest.raises(AssertionError, match="whole index is read"):
                assert_work_indexes(DatabaseWork)
