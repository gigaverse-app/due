"""Root controls for the provider-neutral bulk reducers and round runner, independent of Django storage."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from due.bulk import (
    BulkOutcome,
    BulkOutcomeState,
    BulkResult,
    BulkRetryPolicy,
    BulkStep,
    RetryLimit,
    advance_progress,
    batch_state,
    merge_bulk_evidence,
    pending_members,
    run_bulk,
)
from due.retry_delays import ExponentialDelay


class Verdict(BaseModel):
    note: str


def outcome(key: str, state: BulkOutcomeState, note: str = "") -> BulkOutcome[Verdict]:
    return BulkOutcome(key=key, state=state, result=None if state is BulkOutcomeState.UNKNOWN else Verdict(note=note))


def results(*items: BulkOutcome[Verdict]) -> BulkResult[Verdict]:
    return BulkResult(items=items)


COMPLETE, RETRYABLE, UNKNOWN = BulkOutcomeState.COMPLETE, BulkOutcomeState.RETRYABLE, BulkOutcomeState.UNKNOWN


def test_incremental_receipts_preserve_definite_results_and_unknowns():
    members = ("1", "2")
    first = merge_bulk_evidence(members, results(), results(outcome("1", COMPLETE)))
    assert [item.state for item in first.items] == [COMPLETE, UNKNOWN]
    final = merge_bulk_evidence(members, first, results(outcome("2", COMPLETE)))
    assert final.complete
    # Stale uncertainty and repeated identical evidence are harmless.
    assert merge_bulk_evidence(members, final, results(outcome("1", UNKNOWN), outcome("2", COMPLETE))) == final


@pytest.mark.parametrize(
    "evidence",
    [results(outcome("3", COMPLETE)), results(outcome("1", COMPLETE), outcome("1", COMPLETE))],
)
def test_receipts_cannot_invent_or_repeat_attempt_members(evidence):
    with pytest.raises(ValueError, match="unique members"):
        merge_bulk_evidence(("1",), results(), evidence)


def test_a_receipt_cannot_contradict_an_earlier_definite_verdict():
    stored = merge_bulk_evidence(("1",), results(), results(outcome("1", RETRYABLE)))
    with pytest.raises(ValueError, match="contradicts"):
        merge_bulk_evidence(("1",), stored, results(outcome("1", COMPLETE)))


def test_progress_keeps_completed_members_final_and_ignores_stale_verdicts_for_them():
    keys = ("1", "2")
    progress = advance_progress(keys, results(), results(outcome("1", COMPLETE, "sent"), outcome("2", RETRYABLE)))
    later = advance_progress(keys, progress, results(outcome("1", RETRYABLE), outcome("2", COMPLETE, "sent")))
    assert later.items == (outcome("1", COMPLETE, "sent"), outcome("2", COMPLETE, "sent"))
    with pytest.raises(ValueError, match="cannot be overwritten"):
        advance_progress(keys, later, results(outcome("1", COMPLETE, "bounced")))


def test_retry_subset_keeps_order_and_excludes_uncertain_or_final_members():
    keys = ("3", "1", "2", "4")
    progress = results(outcome("3", RETRYABLE), outcome("1", COMPLETE), outcome("2", UNKNOWN))
    assert pending_members(keys, progress) == ("3", "4")
    assert batch_state(keys, progress) is UNKNOWN
    assert batch_state(("3", "1"), results(outcome("3", RETRYABLE), outcome("1", COMPLETE))) is RETRYABLE
    assert batch_state(("1",), results(outcome("1", COMPLETE))) is COMPLETE


@pytest.mark.parametrize("stop", [BulkStep.STOP, BulkStep.RETRY, BulkStep.CONTINUE])
def test_bulk_runner_has_one_budget_and_only_paces_definite_retries(stop):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    calls = []
    renewed = []
    sleeps = []
    limits = []

    def step():
        calls.append("bulk")
        return stop

    run_bulk(
        step=step,
        ready=lambda: True,
        resume=lambda: renewed.append(True) or True,
        exhausted=limits.append,
        policy=BulkRetryPolicy(3, timedelta(seconds=90), ExponentialDelay(timedelta(seconds=1), timedelta(seconds=5))),
        now=lambda: now,
        sleep=sleeps.append,
        random_fraction=lambda: 0.5,
    )
    assert len(calls) == (1 if stop is BulkStep.STOP else 3)
    assert sleeps == ([0.5, 1.0, 2.0] if stop is BulkStep.RETRY else [])
    assert len(renewed) == (3 if stop is BulkStep.RETRY else 0)
    assert limits == ([] if stop is BulkStep.STOP else [RetryLimit.ROUNDS])


def test_fence_loss_aborts_before_another_batch_or_sleep():
    calls = []
    run_bulk(
        step=lambda: calls.append(True) or BulkStep.RETRY,
        ready=lambda: True,
        resume=lambda: False,
        exhausted=lambda _: pytest.fail("lost ownership is not budget exhaustion"),
        policy=BulkRetryPolicy(3, timedelta(seconds=90)),
        now=lambda: datetime.now(UTC),
        sleep=lambda _: pytest.fail("must stop before sleeping"),
    )
    assert calls == [True]


def test_elapsed_budget_is_checked_before_provider_entry():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    times = iter([start, start + timedelta(seconds=91)])
    limits = []
    run_bulk(
        step=lambda: pytest.fail("time budget expired before call"),
        ready=lambda: True,
        resume=lambda: True,
        exhausted=limits.append,
        policy=BulkRetryPolicy(5, timedelta(seconds=90)),
        now=lambda: next(times),
    )
    assert limits == [RetryLimit.ELAPSED]
