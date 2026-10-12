"""
Competing deliveries exercise real claims and settlement on two connections.

The SQL barrier makes both workers reach their first row-lock request before
one can win. The observer counts actual apply/settlement calls while delegating
their behavior unchanged. Product effects remain the application's observation;
one apply can legitimately contain several provider calls.

Design and usage: docs/architecture.md#verification.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Barrier, Lock
from typing import Any
from unittest.mock import patch

from django.db import connection
from pytest_obligation.evidence.observation import assert_observation
from pytest_obligation.integrations.django.lock_order import locked_tables
from pytest_obligation.integrations.django.writes import leading_keyword

from due.django.conformance.worker_connection import database_worker_connection
from due.django.public import (
    ContinueAfter,
    DurableWorkCapability,
    DurableWorkState,
    ReopenAfter,
    UniverseDurableWork,
)
from due.django.runtime.coordination import WorkExecutionContext


def assert_competing_deliveries[W: UniverseDurableWork, ResultT, ObservationT](
    capability: DurableWorkCapability[W, ResultT],
    work: W,
    *,
    observe: Callable[[], ObservationT],
    expected: ObservationT,
) -> None:
    """Two copies of one queued delivery admit one execution and one fenced decision."""
    work.refresh_from_db()
    initial_attempts = work.attempt_count
    initial_applied = work.applied_revision
    continuation: ContinueAfter | None = None
    settlement_repair: ReopenAfter | None = None
    barrier = Barrier(2, timeout=10)
    counts_lock = Lock()
    arrivals = applies = settlements = 0
    original_apply = type(capability).apply
    original_settle = type(capability)._finalize_success

    def apply(
        self: DurableWorkCapability[W, ResultT], value: W, context: WorkExecutionContext[W]
    ) -> ResultT | ContinueAfter:
        nonlocal applies, continuation
        if self is capability and value.pk == work.pk:
            with counts_lock:
                applies += 1
        result = original_apply(self, value, context)
        if self is capability and value.pk == work.pk and isinstance(result, ContinueAfter):
            continuation = result
        return result

    def settle(self: DurableWorkCapability[W, ResultT], value: W, result: ResultT) -> ReopenAfter | None:
        nonlocal settlements, settlement_repair
        if self is capability and value.pk == work.pk:
            with counts_lock:
                settlements += 1
        settlement_repair = original_settle(self, value, result)
        return settlement_repair

    def run() -> bool:
        nonlocal arrivals
        reached = False

        def synchronize(
            execute: Callable[[str, Any, bool, dict[str, Any]], Any],
            sql: str,
            params: Any,
            many: bool,
            context: dict[str, Any],
        ) -> Any:
            nonlocal reached, arrivals
            if not reached and leading_keyword(sql) == "SELECT" and locked_tables(sql):
                reached = True
                with counts_lock:
                    arrivals += 1
                barrier.wait()
            return execute(sql, params, many, context)

        with database_worker_connection(), connection.execute_wrapper(synchronize):
            return capability.attempt_execution(work.pk, work.fencing_token)

    with (
        patch.object(type(capability), "apply", apply),
        patch.object(type(capability), "_finalize_success", settle),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        futures = [pool.submit(copy_context().run, run) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert arrivals == 2, "both worker connections must reach the real row-lock boundary"
    assert sorted(results) == [False, True], "exactly one copy of the delivery must execute"
    assert applies == 1, "competing deliveries entered application work more than once"
    work.refresh_from_db()
    if continuation is None:
        assert settlements == 1, "competing deliveries must settle product state exactly once"
        if settlement_repair is None:
            assert work.state == DurableWorkState.SUCCEEDED, "successful work must commit completion"
        else:
            assert work.state == DurableWorkState.READY, "settled projection must preserve its repair"
            assert work.lease_expires_at is None, "repair must release the winning claim"
            assert work.last_error_code == settlement_repair.reason
        assert work.applied_revision == work.desired_revision, "settlement must acknowledge the current revision"
    else:
        assert settlements == 0, "a continuation must not publish terminal success"
        assert work.lease_expires_at is None, "a continuation must release the winning claim"
        assert work.state in (DurableWorkState.READY, DurableWorkState.EXHAUSTED), (
            "a continuation must remain owed or expose deadline exhaustion"
        )
        assert work.applied_revision == initial_applied, "waiting is not completion evidence"
    assert work.attempt_count == initial_attempts, "duplicate delivery created another reservation"
    assert_observation(observe(), expected, because="winning worker produces the required application outcome")
