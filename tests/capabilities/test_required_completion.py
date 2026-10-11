"""Completion means committed product output for the requested revision."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from typing import Any
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection, transaction

from due.django.public import (
    CompletionErrors,
    CompletionStatus,
    ContinueAfter,
    DurableWorkState,
    ReopenAfter,
    WorkExecutionContext,
    claim_work,
)
from due.django.testing import (
    HeldNudge,
    WorkerDied,
    die_after,
    expire_lease,
    hold_nudges,
    make_due,
)
from due.errors import WorkIncomplete
from tests.harness import primitive as example
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


@pytest.mark.parametrize("queued", [False, True])
def test_complete_publishes_once_and_accepts_already_completed_work(queued: bool) -> None:
    work = record()
    delivery = (
        claim_work(PrimitiveWork, work.pk, lease_duration=primitive.policy.lease_duration, queued=True)
        if queued
        else None
    )
    primitive.complete(work)
    primitive.complete(work)  # The caller's original snapshot is intentionally stale.
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "after"
    assert PrimitiveEffect.calls == ["after"]
    if delivery is not None:
        assert not primitive.attempt_execution(work.pk, delivery.token)


def test_complete_rejects_superseded_input_without_running_successor() -> None:
    old = record()
    new = record("new", owner=old.owner)
    with pytest.raises(WorkIncomplete) as incomplete:
        primitive.complete(old)
    assert incomplete.value.status is CompletionStatus.SUPERSEDED
    assert PrimitiveEffect.calls == []
    new.refresh_from_db()
    assert new.state == DurableWorkState.READY
    assert new.attempt_count == 0
    primitive.complete(new)
    assert PrimitiveOwner.objects.get(pk=new.owner_id).value == "new"


def test_complete_requires_a_committed_boundary_and_existing_identity() -> None:
    work = record()
    with transaction.atomic(), pytest.raises(RuntimeError, match="autocommit"):
        primitive.complete(work)
    PrimitiveWork.objects.filter(pk=work.pk).delete()
    with pytest.raises(WorkIncomplete) as incomplete:
        primitive.complete(work)
    assert incomplete.value.status is CompletionStatus.MISSING
    assert PrimitiveEffect.calls == []


def test_continuation_is_not_completion() -> None:
    work = record()

    def wait_for_provider(
        self: PrimitiveEffect, current: PrimitiveWork, context: WorkExecutionContext[PrimitiveWork]
    ) -> ContinueAfter:
        return ContinueAfter(delay=timedelta(seconds=1), until=None)

    with patch.object(PrimitiveEffect, "apply", wait_for_provider), pytest.raises(WorkIncomplete):
        primitive.complete(work)
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY
    assert work.applied_revision is None
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"
    make_due(work)
    primitive.complete(work)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "after"


def test_same_revision_repair_is_not_completion() -> None:
    work = record()

    def needs_repair(self: PrimitiveEffect, current: PrimitiveWork, result: str) -> ReopenAfter:
        PrimitiveOwner.objects.filter(pk=current.owner_id).update(value=result)
        return ReopenAfter(delay=timedelta(seconds=1), reason="older_effect_may_arrive")

    with patch.object(PrimitiveEffect, "finalize_success", needs_repair), pytest.raises(WorkIncomplete):
        primitive.complete(work)
    work.refresh_from_db()
    assert work.applied_revision == work.desired_revision
    assert work.state == DurableWorkState.READY


def test_failed_finalizer_rolls_back_and_recovery_publishes_product() -> None:
    work = record()

    def fail_after_product_write(self: PrimitiveEffect, current: PrimitiveWork, result: str) -> None:
        PrimitiveOwner.objects.filter(pk=current.owner_id).update(value=result)
        raise ValueError("publication transaction failed")

    with patch.object(PrimitiveEffect, "finalize_success", fail_after_product_write):
        with pytest.raises(ValueError, match="publication transaction failed"):
            primitive.complete(work)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"
    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert work.applied_revision is None
    assert primitive.recover() == 1
    work_id, token = PrimitiveEffect.deliveries[-1]
    assert primitive.attempt_execution(work_id, token)
    primitive.complete(work)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "after"


def test_busy_completion_does_not_duplicate_an_active_provider_call() -> None:
    work = record()
    entered, release = Event(), Event()

    def slow_provider(current: PrimitiveWork) -> str:
        entered.set()
        assert release.wait(10)
        return current.payload

    def execute() -> None:
        close_old_connections()
        try:
            primitive.complete(work)
        finally:
            close_old_connections()

    with (
        patch.object(example, "call", side_effect=slow_provider) as provider,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        running = pool.submit(execute)
        try:
            assert entered.wait(10)
            with pytest.raises(WorkIncomplete):
                primitive.complete(work)
            assert provider.call_count == 1
            assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"
        finally:
            release.set()
        running.result(timeout=10)
        primitive.complete(work)
        assert provider.call_count == 1
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "after"


def test_lost_fence_cannot_publish_or_report_success() -> None:
    work = record()

    def replaced_during_call(current: PrimitiveWork) -> str:
        record("replacement", owner=current.owner)
        return current.payload

    with patch.object(example, "call", replaced_during_call), pytest.raises(WorkIncomplete):
        primitive.complete(work)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"
    successor = PrimitiveWork.objects.get(pk=work.pk)
    primitive.complete(successor)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "replacement"


@pytest.mark.parametrize("queued", [False, True])
def test_revision_is_rechecked_under_admission_lock(queued: bool) -> None:
    work = record()
    if queued:
        assert claim_work(PrimitiveWork, work.pk, lease_duration=primitive.policy.lease_duration, queued=True)
    replaced = False

    def replace() -> None:
        close_old_connections()
        try:
            record("replacement", owner=PrimitiveOwner.objects.get(pk=work.owner_id))
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:

        def replace_before_lock(
            execute: Callable[[str, Any, bool, dict[str, Any]], Any],
            sql: str,
            params: Any,
            many: bool,
            context: dict[str, Any],
        ) -> object:
            nonlocal replaced
            if not replaced and "FOR UPDATE" in sql and PrimitiveWork._meta.db_table in sql:
                replaced = True
                # Real competing producer commits after execute_now's read,
                # before its claim SELECT acquires the row lock.
                pool.submit(replace).result(timeout=10)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(replace_before_lock), pytest.raises(WorkIncomplete):
            primitive.complete(work)
    assert replaced
    assert PrimitiveEffect.calls == []
    successor = PrimitiveWork.objects.get(pk=work.pk)
    assert successor.state == DurableWorkState.READY
    assert successor.attempt_count == int(queued)
    primitive.complete(successor)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "replacement"


def test_worker_death_preserves_recovery_before_product_completion() -> None:
    work = record()
    with patch.object(example, "call", die_after(example.call)):
        with pytest.raises(WorkerDied):
            primitive.complete(work)
        assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"
        expire_lease(work)
        assert primitive.recover() == 1
        work_id, token = PrimitiveEffect.deliveries[-1]
        assert primitive.attempt_execution(work_id, token)
    primitive.complete(work)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "after"


def test_complete_or_defer_reports_completion_without_repeating_the_effect() -> None:
    work = record()
    assert primitive.complete_or_defer(work).completed
    assert primitive.complete_or_defer(work).completed
    assert PrimitiveEffect.calls == ["after"]
    assert primitive.complete_or_defer(None).status is CompletionStatus.NOT_ADMITTED


@pytest.mark.parametrize("commit", [True, False], ids=["commit", "rollback"])
def test_enclosing_transaction_defers_provider_io_and_restores_the_nudge(commit: bool) -> None:
    # A synchronous producer suppresses its nudge because it will attempt the
    # work itself. Inside a caller's transaction that attempt would precede the
    # commit, so the worker must be told instead, and only if the commit happens.
    owner = PrimitiveOwner.objects.create()
    with hold_nudges() as held:
        with transaction.atomic():
            work = record(owner=owner)
            assert primitive.complete_or_defer(work).status is CompletionStatus.DEFERRED
            assert PrimitiveEffect.calls == []
            assert held == []
            transaction.set_rollback(not commit)
    assert PrimitiveEffect.calls == []
    assert held == ([HeldNudge(PrimitiveWork._meta.label_lower, work.pk)] if commit else [])
    assert PrimitiveWork.objects.filter(owner=owner).exists() is commit


def test_superseded_revision_is_a_status_for_the_caller_to_judge() -> None:
    old = record()
    new = record("new", owner=old.owner)
    assert primitive.complete_or_defer(old).status is CompletionStatus.SUPERSEDED
    assert PrimitiveEffect.calls == []
    assert primitive.complete_or_defer(new).completed
    assert PrimitiveOwner.objects.get(pk=new.owner_id).value == "new"


def test_reservation_held_elsewhere_is_in_progress_without_a_second_call() -> None:
    work = record()
    assert claim_work(PrimitiveWork, work.pk, lease_duration=primitive.policy.lease_duration)
    assert primitive.complete_or_defer(work).status is CompletionStatus.IN_PROGRESS
    assert PrimitiveEffect.calls == []


@pytest.mark.parametrize("errors", list(CompletionErrors))
def test_handler_failure_policy_keeps_the_revision_owed(errors: CompletionErrors) -> None:
    work = record()
    failure = ConnectionError("provider unavailable")
    with patch.object(example, "call", side_effect=failure):
        if errors is CompletionErrors.RAISE:
            with pytest.raises(ConnectionError):
                primitive.complete_or_defer(work, errors=errors)
        else:
            outcome = primitive.complete_or_defer(work, errors=errors)
            assert (outcome.status, outcome.error) == (CompletionStatus.OWED, failure)
    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"
    assert primitive.complete_or_defer(work).completed
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "after"
