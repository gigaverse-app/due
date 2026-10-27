"""Bulk calls use the ordinary Due claim, attempt, settlement and recovery path."""

from dataclasses import replace
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from due.bulk import BulkOutcomeState, BulkResult, BulkRetryPolicy, RejectionResponse
from due.django.capabilities.bulk import BulkRetryNeeded, CapturedBatch
from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState, EffectAttempt, EffectAttemptOutcome
from due.django.runtime import coordination
from due.django.runtime.coordination import ContinueAfter
from due.django.testing import WorkerDied, die_after, expire_lease, make_due
from due.errors import EffectNotAppliedError, EffectOutcomeUnknownError, EffectRejection
from due.retry_delays import ExponentialDelay
from tests.harness.bulk import (
    BatchOperation,
    BatchReceipt,
    BatchWork,
    ObservedBatchOperation,
    observed_batch,
    record,
    result,
)
from tests.harness.bulk import batch_environment as batch_environment
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = pytest.mark.django_db(transaction=True)


def test_one_bulk_call_completes_one_due_work(batch_operation: BatchOperation) -> None:
    work = record(batch_operation)
    with CaptureQueriesContext(connection) as queries:
        assert batch_operation.attempt_execution(work.pk)
    # Claim, provider entry, progress and completion each validate under locks.
    # A finished round needs no unlocked refresh between those transitions.
    unlocked_reads = [
        query["sql"]
        for query in queries
        if query["sql"].startswith("SELECT")
        and f'FROM "{BatchWork._meta.db_table}"' in query["sql"]
        and "FOR UPDATE" not in query["sql"]
    ]
    assert unlocked_reads == [], unlocked_reads
    assert sum(query["sql"] == "COMMIT" for query in queries) == 4
    # Elapsed retry pacing is process-local; only durable authority and
    # evidence timestamps use database time. Only provider entry reads it
    # separately; claim, renewal, receipt and completion return statement time.
    assert sum("clock_timestamp()" in query["sql"] for query in queries) == 1
    # Ingestion and locked completion each read the receipt. Routing between
    # them reuses the canonical value returned by the committed ingestion.
    receipt_reads = [
        query["sql"] for query in queries if query["sql"].startswith(f'SELECT "{BatchReceipt._meta.db_table}".')
    ]
    assert len(receipt_reads) == 2, receipt_reads
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.applied_revision == work.desired_revision
    assert work.published_keys == ["a", "b", "c"]
    progress_writes = [
        q["sql"]
        for q in queries
        if q["sql"].startswith(f'UPDATE "{BatchWork._meta.db_table}"') and '"bulk_progress" =' in q["sql"]
    ]
    assert len(progress_writes) == 1
    assert '"published_keys" =' in progress_writes[0]
    assert BatchOperation.calls == [("a", "b", "c")]
    attempt = attempts_for(work).get()
    assert attempt.outcome == EffectAttemptOutcome.APPLIED
    assert not attempt.repair_pending
    assert BatchReceipt.objects.get(attempt=attempt).members == ["a", "b", "c"]


@pytest.mark.parametrize("subset_retry", [False, True])
def test_completion_failure_rolls_back_progress_but_keeps_provider_receipt(
    batch_operation: BatchOperation, monkeypatch: pytest.MonkeyPatch, subset_retry: bool
) -> None:
    work = record(batch_operation)
    if subset_retry:
        BatchOperation.script.append(
            BulkResult(
                items=(
                    *result("a", "c").items,
                    *result("b", state=BulkOutcomeState.RETRYABLE).items,
                )
            )
        )
    prior_progress = ["a", "c"] if subset_retry else []
    expected_calls = [("a", "b", "c"), ("b",)] if subset_retry else [("a", "b", "c")]

    def fail_completion(*args: object, **kwargs: object) -> None:
        raise RuntimeError("product completion failed")

    with monkeypatch.context() as failure:
        failure.setattr(BatchOperation, "finalize_success", fail_completion)
        with pytest.raises(RuntimeError, match="product completion failed"):
            batch_operation.attempt_execution(work.pk)
        # Repeated repair failures keep all accepted members durably owed.
        # No recovery iteration may send the already-accepted members again.
        for _ in range(3):
            work.refresh_from_db()
            assert work.state == DurableWorkState.UNKNOWN
            assert work.published_keys == prior_progress
            assert [item.key for item in batch_operation.completed_items(work)] == prior_progress
            attempt = attempts_for(work).order_by("started_at", "pk").last()
            assert attempt is not None and attempt.repair_pending
            assert BatchReceipt.objects.get(attempt=attempt).settlement_pending
            assert batch_operation.evidence_store.read(attempt) == (
                result("b") if subset_retry else result("a", "b", "c")
            )
            assert BatchOperation.calls == expected_calls
            assert sorted(BatchOperation.accepted) == ["a", "b", "c"]
            with pytest.raises(RuntimeError, match="product completion failed"):
                batch_operation.settle_from_applied_effect_evidence(work.pk)
    batch_operation.recover()
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert sorted(work.published_keys) == ["a", "b", "c"]
    assert sorted(item.key for item in batch_operation.completed_items(work)) == ["a", "b", "c"]
    assert BatchOperation.calls == expected_calls
    assert sorted(BatchOperation.accepted) == ["a", "b", "c"]
    assert not attempts_for(work).filter(repair_pending=True).exists()
    assert not BatchReceipt.objects.filter(attempt__in=attempts_for(work), settlement_pending=True).exists()


def test_only_definite_rejections_reenter_the_bulk_provider(batch_operation: BatchOperation) -> None:
    BatchOperation.script.append(
        BulkResult(
            items=(
                *result("a", "c").items,
                *result("b", state=BulkOutcomeState.RETRYABLE).items,
            )
        )
    )
    work = record(batch_operation)
    assert batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert BatchOperation.calls == [("a", "b", "c"), ("b",)]
    assert sorted(BatchOperation.accepted) == ["a", "b", "c"]
    assert attempts_for(work).count() == 2
    assert not attempts_for(work).filter(repair_pending=True).exists()


def test_partial_receipts_preserve_uncertainty_and_repair_without_resending(batch_operation: BatchOperation) -> None:
    BatchOperation.script.append(result("a"))
    work = record(batch_operation)
    with pytest.raises(EffectOutcomeUnknownError):
        batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.UNKNOWN
    assert [item.key for item in batch_operation.completed_items(work)] == ["a"]
    assert work.published_keys == ["a"]
    attempt = attempts_for(work).get()
    assert attempt.outcome == EffectAttemptOutcome.UNKNOWN
    batch_operation.record_applied_effect_evidence(attempt, result("b"))
    batch_operation.derive()
    work.refresh_from_db()
    assert work.state == DurableWorkState.UNKNOWN
    assert [item.key for item in batch_operation.completed_items(work)] == ["a", "b"]
    assert work.published_keys == ["a", "b"]
    batch_operation.record_applied_effect_evidence(attempt, result("c"))
    batch_operation.derive()
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert BatchOperation.calls == [("a", "b", "c")]
    assert work.published_keys == ["a", "b", "c"]
    assert not attempts_for(work).filter(repair_pending=True).exists()


@pytest.mark.parametrize("bad_keys", [("a", "a"), ("foreign",)])
def test_receipt_membership_is_exact(batch_operation: BatchOperation, bad_keys: tuple[str, ...]) -> None:
    BatchOperation.script.append(result("a"))
    work = record(batch_operation)
    with pytest.raises(EffectOutcomeUnknownError):
        batch_operation.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    with pytest.raises(ValueError):
        batch_operation.record_applied_effect_evidence(attempt, result(*bad_keys))
    work.refresh_from_db()
    assert [item.key for item in batch_operation.completed_items(work)] == ["a"]
    assert BatchOperation.calls == [("a", "b", "c")]


def test_full_rejection_cannot_erase_an_accepted_member(batch_operation: BatchOperation) -> None:
    BatchOperation.script.append(result("a"))
    work = record(batch_operation)
    with pytest.raises(EffectOutcomeUnknownError):
        batch_operation.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    with pytest.raises(ValueError, match="contradicts"):
        batch_operation.record_rejection(attempt, EffectRejection(message="nothing applied"))
    attempt.refresh_from_db()
    assert attempt.outcome == EffectAttemptOutcome.UNKNOWN
    assert batch_operation.evidence_store.read(attempt).items[0] == result("a").items[0]


def test_death_after_receipt_still_owes_work_completion(
    batch_operation: BatchOperation, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = record(batch_operation)
    with monkeypatch.context() as fault, CaptureQueriesContext(connection) as queries:

        def die_before_completion(*args: object, **kwargs: object) -> None:
            raise WorkerDied("receipt committed before claim completion")

        fault.setattr(coordination, "complete_work", die_before_completion)
        with pytest.raises(WorkerDied):
            batch_operation.attempt_execution(work.pk)
    assert attempts_for(work).get().repair_pending
    assert BatchReceipt.objects.get(attempt=attempts_for(work).get()).settlement_pending
    # Preserve repair debt across this crash without rewriting the already-true
    # attempt or receipt flag during member settlement. Evidence acceptance
    # sets both once; the receipt remains discoverable until final completion.
    redundant = [
        query["sql"]
        for query in queries
        if query["sql"].startswith(
            (
                'UPDATE "due_effectattempt" SET "repair_pending" = true',
                f'UPDATE "{BatchReceipt._meta.db_table}" SET "settlement_pending" = true',
            )
        )
    ]
    assert redundant == [], redundant
    expire_lease(work)
    batch_operation.recover()
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert not attempts_for(work).get().repair_pending
    assert not BatchReceipt.objects.get(attempt=attempts_for(work).get()).settlement_pending
    assert BatchOperation.calls == [("a", "b", "c")]


def test_rejection_recovery_ignores_already_settled_previous_members(
    batch_operation: BatchOperation, monkeypatch: pytest.MonkeyPatch
) -> None:
    BatchOperation.script.extend(
        [
            BulkResult(items=(*result("a").items, *result("b", "c", state=BulkOutcomeState.RETRYABLE).items)),
            EffectNotAppliedError("second call rejected"),
        ]
    )
    work = record(batch_operation)
    with monkeypatch.context() as fault:
        fault.setattr(BatchOperation, "record_rejection", die_after(BatchOperation.record_rejection))
        with pytest.raises(WorkerDied):
            batch_operation.attempt_execution(work.pk)
    expire_lease(work)
    batch_operation.recover()
    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert work.rearm_count == 1
    make_due(work)
    batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert BatchOperation.calls == [("a", "b", "c"), ("b", "c"), ("b", "c")]
    assert BatchOperation.accepted_by_work[work.pk] == ["a", "b", "c"]


def test_late_whole_call_rejection_resolves_every_unknown_member(batch_operation: BatchOperation) -> None:
    BatchOperation.script.append(result("b", state=BulkOutcomeState.RETRYABLE))
    work = record(batch_operation)
    with pytest.raises(EffectOutcomeUnknownError):
        batch_operation.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    batch_operation.record_rejection(attempt, EffectRejection(message="provider proves the entire call was rejected"))
    batch_operation.recover()
    make_due(work)
    batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert BatchOperation.calls == [("a", "b", "c"), ("a", "b", "c")]
    assert BatchOperation.accepted_by_work[work.pk] == ["a", "b", "c"]


@pytest.mark.parametrize("after_first_round", [False, True])
def test_product_pause_preserves_progress_without_spending_retry_budget(
    batch_operation: BatchOperation, monkeypatch: pytest.MonkeyPatch, after_first_round: bool
) -> None:
    work = record(batch_operation)
    if after_first_round:
        BatchOperation.script.append(
            BulkResult(items=(*result("a").items, *result("b", "c", state=BulkOutcomeState.RETRYABLE).items))
        )
    with monkeypatch.context() as pause:
        pause.setattr(
            BatchOperation,
            "before_batch",
            lambda self, work: (
                ContinueAfter(timedelta(minutes=1), until=None) if len(self.calls) >= int(after_first_round) else None
            ),
        )
        assert batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY
    assert work.rearm_count == 0
    assert len(BatchOperation.calls) == int(after_first_round)
    make_due(work)
    assert batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert BatchOperation.accepted_by_work[work.pk] == ["a", "b", "c"]
    assert BatchOperation.calls[-1] == (("b", "c") if after_first_round else ("a", "b", "c"))


def test_operator_resume_retains_accepted_members_and_refuses_uncertainty(batch_operation: BatchOperation) -> None:
    limited = replace(
        batch_operation,
        policy=replace(batch_operation.policy, max_rearms=0),
        bulk_policy=replace(batch_operation.bulk_policy, max_rounds=1),
    )
    BatchOperation.script.append(
        BulkResult(items=(*result("a").items, *result("b", "c", state=BulkOutcomeState.RETRYABLE).items))
    )
    work = record(limited)
    with pytest.raises(BulkRetryNeeded):
        limited.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.EXHAUSTED
    assert not limited.resume_pending(work.pk, notify_worker=False)
    assert limited.resume_pending(work.pk, reset_budget=True, notify_worker=False)
    assert limited.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert BatchOperation.calls == [("a", "b", "c"), ("b", "c")]
    assert not limited.resume_pending(work.pk, reset_budget=True, notify_worker=False)

    BatchOperation.script.append(result("a"))
    uncertain = record(limited)
    with pytest.raises(EffectOutcomeUnknownError):
        limited.attempt_execution(uncertain.pk)
    assert not limited.resume_pending(uncertain.pk, reset_budget=True, notify_worker=False)
    uncertain.refresh_from_db()
    assert uncertain.state == DurableWorkState.UNKNOWN


@pytest.mark.parametrize(
    "rearms,retry_pending,state,expected_rearms",
    [
        (1, False, DurableWorkState.READY, 1),
        (1, True, DurableWorkState.RETRYABLE, 2),
        (2, True, DurableWorkState.EXHAUSTED, 2),
    ],
)
def test_import_preserves_legacy_retry_history(batch_operation, rearms, retry_pending, state, expected_rearms):
    from django.db import transaction

    from due.django.runtime.coordination import database_now

    work = record(batch_operation)
    available = database_now() + timedelta(minutes=3)
    with transaction.atomic():
        batch_operation.import_retry_history(work, rearms=rearms, retry_pending=retry_pending, not_before=available)
    work.refresh_from_db()
    assert work.state == state
    assert work.rearm_count == expected_rearms
    if state != DurableWorkState.EXHAUSTED:
        assert work.available_at == available
    assert not batch_operation.attempt_execution(work.pk)
    assert BatchOperation.calls == []


def test_retry_import_cannot_rewrite_an_executed_occurrence(batch_operation):
    from django.db import transaction

    work = record(batch_operation)
    batch_operation.attempt_execution(work.pk)
    with transaction.atomic(), pytest.raises(ValueError, match="unexecuted"):
        batch_operation.import_retry_history(work, rearms=0, retry_pending=False)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert BatchOperation.calls == [("a", "b", "c")]


def test_a_preparation_failure_is_a_definite_nonapplication_without_a_provider_call(
    batch_operation: BatchOperation,
) -> None:
    BatchOperation.prepare_failure = KeyError("template variable")
    work = record(batch_operation)
    with pytest.raises(EffectNotAppliedError, match="batch preparation failed: KeyError"):
        batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    # Nothing reached the provider, so recovery may resend every member.
    assert work.state == DurableWorkState.RETRYABLE
    assert BatchOperation.calls == []
    assert attempts_for(work).get().outcome == EffectAttemptOutcome.NOT_APPLIED
    BatchOperation.prepare_failure = None
    make_due(work)
    assert batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert BatchOperation.calls == [("a", "b", "c")]


def test_a_whole_call_rejection_records_nonapplication_and_may_retry_inline(batch_operation: BatchOperation) -> None:
    BatchOperation.rejection_response = RejectionResponse.RETRY_INLINE
    BatchOperation.script.extend(
        [
            BulkResult(items=(*result("a").items, *result("b", "c", state=BulkOutcomeState.RETRYABLE).items)),
            EffectNotAppliedError("throttled"),
        ]
    )
    work = record(batch_operation)
    assert batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.rearm_count == 0
    assert BatchOperation.calls == [("a", "b", "c"), ("b", "c"), ("b", "c")]
    assert sorted(attempts_for(work).values_list("outcome", flat=True)) == sorted(
        [EffectAttemptOutcome.APPLIED, EffectAttemptOutcome.NOT_APPLIED, EffectAttemptOutcome.APPLIED]
    )
    assert BatchOperation.published_rejections == ["throttled"]
    assert BatchOperation.accepted_by_work[work.pk] == ["a", "b", "c"]


def test_by_default_a_whole_call_rejection_spends_the_core_retry_budget(batch_operation: BatchOperation) -> None:
    BatchOperation.script.append(EffectNotAppliedError("throttled"))
    work = record(batch_operation)
    with pytest.raises(EffectNotAppliedError):
        batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert work.rearm_count == 1
    assert BatchOperation.calls == [("a", "b", "c")]
    assert BatchOperation.published_rejections == ["throttled"]


def test_a_rate_limited_call_pauses_until_reset_without_spending_budget(batch_operation: BatchOperation) -> None:
    BatchOperation.rejection_response = ContinueAfter(timedelta(seconds=30), until=None)
    BatchOperation.script.append(EffectNotAppliedError("429: retry after 30s"))
    work = record(batch_operation)
    assert batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY
    assert work.rearm_count == 0
    assert batch_operation.attempt_execution(work.pk) is False  # Not due until the reset passes.
    make_due(work)
    assert batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert BatchOperation.calls == [("a", "b", "c"), ("a", "b", "c")]


def test_death_between_inline_rejections_settles_only_the_open_one(
    batch_operation: BatchOperation, monkeypatch: pytest.MonkeyPatch
) -> None:
    BatchOperation.rejection_response = RejectionResponse.RETRY_INLINE
    BatchOperation.script.extend([EffectNotAppliedError("first"), EffectNotAppliedError("second")])
    work = record(batch_operation)
    recorded = []
    original = BatchOperation.record_rejection

    def die_on_second(self: BatchOperation, attempt: EffectAttempt, rejection: EffectRejection) -> EffectRejection:
        decision = original(self, attempt, rejection)
        recorded.append(decision.message)
        if len(recorded) == 2:
            raise WorkerDied("after the second rejection committed")
        return decision

    with monkeypatch.context() as fault:
        fault.setattr(BatchOperation, "record_rejection", die_on_second)
        with pytest.raises(WorkerDied):
            batch_operation.attempt_execution(work.pk)
    expire_lease(work)
    batch_operation.recover()
    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert BatchOperation.published_rejections == ["first", "second"]
    make_due(work)
    assert batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert BatchOperation.accepted_by_work[work.pk] == ["a", "b", "c"]


def test_an_uncertain_call_is_resolved_by_provider_lookup(batch_operation: BatchOperation) -> None:
    BatchOperation.script.append(result("a"))
    work = record(observed_batch)
    with pytest.raises(EffectOutcomeUnknownError):
        observed_batch.attempt_execution(work.pk)
    observed_batch.derive()  # Lookup has nothing yet: the call stays uncertain.
    work.refresh_from_db()
    assert work.state == DurableWorkState.UNKNOWN
    ObservedBatchOperation.observations.append(result("b", "c"))
    observed_batch.derive()
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert BatchOperation.calls == [("a", "b", "c")]


def test_a_backoff_sleep_must_fit_inside_the_lease(batch_operation: BatchOperation) -> None:
    with pytest.raises(ValueError, match="shorter than the work lease"):
        replace(
            batch_operation,
            bulk_policy=BulkRetryPolicy(
                3, timedelta(seconds=30), ExponentialDelay(timedelta(seconds=1), batch_operation.policy.lease_duration)
            ),
        )


def test_a_rejection_after_the_claim_was_lost_mid_call_is_settled_by_recovery(
    batch_operation: BatchOperation, monkeypatch: pytest.MonkeyPatch
) -> None:
    BatchOperation.rejection_response = RejectionResponse.RETRY_INLINE
    work = record(batch_operation)

    def refused_after_reclaim(self: BatchOperation, work: BatchWork, batch: CapturedBatch, payload: None) -> None:
        # The lease expires while the provider is deciding; recovery reclaims.
        expire_lease(work)
        batch_operation.recover()
        raise EffectNotAppliedError("throttled")

    monkeypatch.setattr(BatchOperation, "send_batch", refused_after_reclaim)
    with pytest.raises(EffectNotAppliedError):
        batch_operation.attempt_execution(work.pk)
    assert BatchOperation.published_rejections == []
    batch_operation.derive()
    work.refresh_from_db()
    # The committed NOT_APPLIED decision is settled once, by recovery.
    assert work.state == DurableWorkState.RETRYABLE
    assert BatchOperation.published_rejections == ["throttled"]
    monkeypatch.undo()
    make_due(work)
    assert batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
