"""Provider-neutral batch progress shared by every bulk capability.

A batch is one fixed list of string member keys. Each provider call reports a
verdict per member: COMPLETE (a definite final outcome, including a permanent
rejection), RETRYABLE (definitely not applied, may be sent again) or UNKNOWN
(may have been applied). This module owns the only reducers for those verdicts;
capabilities decide what a verdict permits. No database or Django import here.
"""

import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from time import sleep as time_sleep

from pydantic import BaseModel

from due.retry_delays import ExponentialDelay


class BulkOutcomeState(StrEnum):
    COMPLETE = "complete"
    RETRYABLE = "retryable"
    UNKNOWN = "unknown"


class BulkOutcome[ResultT: BaseModel](BaseModel):
    key: str
    state: BulkOutcomeState
    result: ResultT | None = None


class BulkResult[ResultT: BaseModel](BaseModel):
    items: tuple[BulkOutcome[ResultT], ...]

    @property
    def complete(self) -> bool:
        """Every attempted member has a definite verdict, including rejections."""
        return all(item.state is not BulkOutcomeState.UNKNOWN for item in self.items)


def merge_bulk_evidence[ResultT: BaseModel](
    members: Sequence[str], stored: BulkResult[ResultT], received: BulkResult[ResultT]
) -> BulkResult[ResultT]:
    """One provider call's evidence: uncertainty can resolve, definite facts cannot change.

    Fragments may arrive late, duplicated or reordered. Every member of the call
    appears in the result; a member with no evidence yet is UNKNOWN.
    """
    keys = [item.key for item in received.items]
    if len(keys) != len(set(keys)) or not set(keys) <= set(members):
        raise ValueError("bulk evidence must name unique members of this exact attempt")
    merged = {item.key: item for item in stored.items}
    for item in received.items:
        previous = merged.get(item.key)
        if previous is not None and previous.state is not BulkOutcomeState.UNKNOWN:
            if item.state is not BulkOutcomeState.UNKNOWN and previous != item:
                raise ValueError("bulk evidence contradicts an earlier definite outcome")
            continue  # Stale uncertainty cannot erase definite evidence.
        merged[item.key] = item
    return BulkResult(
        items=tuple(merged.get(key, BulkOutcome(key=key, state=BulkOutcomeState.UNKNOWN)) for key in members)
    )


def advance_progress[ResultT: BaseModel](
    keys: Sequence[str], progress: BulkResult[ResultT], result: BulkResult[ResultT]
) -> BulkResult[ResultT]:
    """Progress across calls: a COMPLETE member is final; any other verdict is replaced by the newer call.

    A later non-final verdict for a COMPLETE member is stale evidence and is
    ignored; a different final verdict is a contradiction.
    """
    by_key = {item.key: item for item in progress.items}
    for item in result.items:
        previous = by_key.get(item.key)
        if previous is not None and previous.state is BulkOutcomeState.COMPLETE:
            if item.state is BulkOutcomeState.COMPLETE and item != previous:
                raise ValueError("a completed batch member cannot be overwritten")
            continue
        by_key[item.key] = item
    return BulkResult(items=tuple(by_key[key] for key in keys if key in by_key))


def pending_members[ResultT: BaseModel](keys: Sequence[str], progress: BulkResult[ResultT]) -> tuple[str, ...]:
    """Members a new call may carry, in submission order: no verdict yet, or definitely RETRYABLE."""
    states = {item.key: item.state for item in progress.items}
    return tuple(key for key in keys if states.get(key, BulkOutcomeState.RETRYABLE) is BulkOutcomeState.RETRYABLE)


def batch_state[ResultT: BaseModel](keys: Sequence[str], progress: BulkResult[ResultT]) -> BulkOutcomeState:
    """UNKNOWN while any member may have applied, else RETRYABLE while any remains, else COMPLETE."""
    if not progress.complete:
        return BulkOutcomeState.UNKNOWN
    return BulkOutcomeState.RETRYABLE if pending_members(keys, progress) else BulkOutcomeState.COMPLETE


class BulkStep(StrEnum):
    STOP = "stop"
    RETRY = "retry"
    """Definite rejection: renew ownership, reload progress, then back off."""
    CONTINUE = "continue"
    """Local validation removed an item before sending: recheck without provider backoff."""


class RejectionResponse(StrEnum):
    """What a capability does after a provider definitely rejected a whole call."""

    RETRY_LATER = "retry_later"
    """Hand the members to the core retry policy: spends one retry and waits its delay."""
    RETRY_INLINE = "retry_inline"
    """Back off and resend within this claim's bulk rounds, e.g. a short provider throttle."""


class RetryLimit(StrEnum):
    ROUNDS = "round_budget"
    ELAPSED = "wall_clock"


@dataclass(frozen=True)
class BulkRetryPolicy:
    max_rounds: int
    wall_clock_budget: timedelta
    delay: ExponentialDelay | None = None


def run_bulk(
    *,
    step: Callable[[], BulkStep],
    ready: Callable[[], bool],
    resume: Callable[[], bool],
    exhausted: Callable[[RetryLimit], None],
    policy: BulkRetryPolicy,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] = time_sleep,
    random_fraction: Callable[[], float] = random.random,
) -> None:
    """Run bounded rounds on one claim, never a per-item provider call.

    ready() rechecks product eligibility before each attempt; step() persists
    its attempt and definite results and returns STOP on uncertainty or lost
    authority. RETRY is permission only for definitely unsent members.
    resume() renews the same fence and reloads committed progress before another
    round. exhausted() durably hands work to existing recovery. Exceptions
    propagate, leaving the committed attempt for that recovery to interpret.
    No database transaction is opened or held across callbacks or sleep.
    The elapsed budget uses a local monotonic clock by default. An explicit
    ``now`` retains the existing datetime clock contract for callers that need
    it. This timer never grants ownership or sets persisted deadlines.
    """
    if now is None:
        started = time.monotonic()

        def elapsed() -> timedelta:
            return timedelta(seconds=time.monotonic() - started)
    else:
        started_at = now()

        def elapsed() -> timedelta:
            return now() - started_at

    retries = 0
    for _ in range(policy.max_rounds):
        if not ready():
            return
        if elapsed() > policy.wall_clock_budget:
            exhausted(RetryLimit.ELAPSED)
            return
        result = step()
        if result is BulkStep.STOP:
            return
        if result is BulkStep.CONTINUE:
            continue
        if result is not BulkStep.RETRY:
            raise ValueError("bulk step must explicitly stop, retry or continue")
        if not resume():
            return
        ceiling = policy.delay.at(retries).total_seconds() if policy.delay is not None else 0
        sleep(random_fraction() * ceiling)
        retries += 1
    exhausted(RetryLimit.ROUNDS)
