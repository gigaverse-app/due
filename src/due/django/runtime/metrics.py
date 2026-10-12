"""Shared observations of backlog, attempts, state and recovery.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from collections.abc import Callable
from enum import StrEnum
from functools import wraps
from typing import TypedDict

from celery.exceptions import SoftTimeLimitExceeded
from prometheus_client import Counter, Gauge

from due.django.models import DurableWorkState, EffectAttemptOutcome
from due.observability import capture_exception_safely


def _optional_observation[**P](observe: Callable[P, None]) -> Callable[P, None]:
    @wraps(observe)
    def record(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            observe(*args, **kwargs)
        except SoftTimeLimitExceeded:
            # Celery's orderly-stop request, not a telemetry failure: the task must stop.
            raise
        except Exception as error:
            # These callbacks run inside claims and settlement transactions too.
            # Telemetry cannot revoke their authority or roll back product truth.
            capture_exception_safely(error)

    return record


class DurableWorkOperation(StrEnum):
    CLAIMED = "claimed"
    CLAIM_MISSED = "claim_missed"
    INELIGIBLE_DELIVERY = "ineligible_delivery"
    RENEWED = "renewed"
    OWNERSHIP_LOST = "ownership_lost"
    SUCCEEDED = "succeeded"
    RETRYABLE = "retryable"
    UNKNOWN = "unknown"
    TERMINAL_FAILED = "terminal_failed"
    EXHAUSTED = "exhausted"
    REAPED = "reaped"
    DISPATCHED = "dispatched"
    SUPERSEDED = "superseded"
    PRUNED = "pruned"
    CONTINUED = "continued"


class RecoveryPhase(StrEnum):
    """One named step of a recovery tick; a bounded metric label."""

    REPAIR_WINDOW = "repair_window"
    DISCOVERY = "discovery"
    EVIDENCE_SETTLEMENT = "evidence_settlement"
    NONAPPLICATION = "nonapplication"
    OBSERVATION = "observation"
    STALE_WRITE_REOPEN = "stale_write_reopen"
    ATTEMPT_RESOLUTION = "attempt_resolution"
    ATTEMPT_RETENTION = "attempt_retention"
    ATTEMPT_CENSUS = "attempt_census"
    REAP = "reap"
    DISPATCH = "dispatch"
    DEADLINE_WAKEUP = "deadline_wakeup"
    CENSUS = "census"
    RETENTION = "retention"


class RecoveryOutcome(StrEnum):
    CLEAN = "clean"
    #: Every phase ran; at least one identity or phase failed and was isolated.
    FAILED = "failed"
    #: A failure that ends the tick escaped (soft time limit, unwired slot, lost lock).
    ABORTED = "aborted"


class DurableWorkLabels(TypedDict):
    effect: str
    operation: DurableWorkOperation


class DurableWorkStateLabels(TypedDict):
    effect: str
    state: DurableWorkState


# `effect` comes only from registered Django model labels. It is finite code
# configuration, never work/provider/user data, so the series set stays bounded
# by the number of adopters.
_due_operations_total = Counter(
    "due_operations_total",
    "Durable-effect coordination operations by registered work type and outcome.",
    labelnames=("effect", "operation"),
)
_due_backlog = Gauge(
    "due_backlog",
    "Active and failed durable effects by work type and state; retained successes are excluded.",
    labelnames=("effect", "state"),
    multiprocess_mode="livemostrecent",
)
_due_oldest_age_seconds = Gauge(
    "due_oldest_age_seconds",
    "Age of the oldest active or failed durable effect by work type and state; successes are excluded.",
    labelnames=("effect", "state"),
    multiprocess_mode="livemostrecent",
)

_due_outstanding = Gauge(
    "due_outstanding",
    "All active durable obligations, including queued and delayed retries.",
    labelnames=("effect",),
    multiprocess_mode="livemostrecent",
)

_due_last_recovery = Gauge(
    "due_last_recovery_timestamp_seconds",
    "Last completed recovery tick; distinguishes an idle effect from a stalled scheduler.",
    labelnames=("effect",),
    multiprocess_mode="livemostrecent",
)

_due_last_unclean_recovery = Gauge(
    "due_last_unclean_recovery_timestamp_seconds",
    "Last failed or aborted recovery tick; newer than the clean heartbeat means recovery is still unclean.",
    labelnames=("effect",),
    multiprocess_mode="livemostrecent",
)

_due_recovery_stale_after = Gauge(
    "due_recovery_stale_after_seconds",
    "How long an effect may go without a clean recovery tick before it is stale: twice its slowest cadence.",
    labelnames=("effect",),
    multiprocess_mode="livemostrecent",
)

_due_pending_attempts = Gauge(
    "due_pending_provider_attempts",
    "Provider attempts whose possible or known writes still require reconciliation.",
    labelnames=("effect", "outcome"),
    multiprocess_mode="livemostrecent",
)


@_optional_observation
def record_pending_effect_attempts(effect: str, outcome: EffectAttemptOutcome, count: int) -> None:
    _due_pending_attempts.labels(effect=effect, outcome=outcome).set(count)


@_optional_observation
def record_recovery_completed(effect: str, timestamp: float) -> None:
    _due_last_recovery.labels(effect=effect).set(timestamp)


@_optional_observation
def record_recovery_stale_after(effect: str, seconds: float) -> None:
    _due_recovery_stale_after.labels(effect=effect).set(seconds)


_due_recovery_failures_total = Counter(
    "due_recovery_failures_total",
    "Identities (or whole phases) a recovery tick isolated after their step raised; each also fails the tick.",
    labelnames=("effect", "phase"),
)
_due_recovery_passes_total = Counter(
    "due_recovery_passes_total",
    "Recovery ticks by outcome; a failed tick still ran every phase, an aborted one stopped early.",
    labelnames=("effect", "outcome"),
)


_due_recovery_deferred = Gauge(
    "due_recovery_deferred",
    "Identities a recovery phase isolated and is holding out with backoff; each is still owed.",
    labelnames=("effect", "phase"),
    multiprocess_mode="livemostrecent",
)
_due_recovery_deferred_oldest_age_seconds = Gauge(
    "due_recovery_deferred_oldest_age_seconds",
    "Age of the oldest first failure among an effect's deferred identities, per phase.",
    labelnames=("effect", "phase"),
    multiprocess_mode="livemostrecent",
)


@_optional_observation
def record_recovery_deferrals(effect: str, phase: RecoveryPhase, *, count: int, oldest_age_seconds: float) -> None:
    _due_recovery_deferred.labels(effect=effect, phase=phase).set(count)
    _due_recovery_deferred_oldest_age_seconds.labels(effect=effect, phase=phase).set(oldest_age_seconds)


@_optional_observation
def prepare_recovery_series(effect: str, phase: RecoveryPhase | None = None) -> None:
    """
    Create an effect's tick counters (or one phase's failure counter) at zero.

    ``increase()`` cannot see a counter's first step when the series appears
    already at 1, so without this a worker process's first failed or aborted
    tick of an effect would never page.
    """
    if phase is None:
        for outcome in RecoveryOutcome:
            _due_recovery_passes_total.labels(effect=effect, outcome=outcome)
    else:
        _due_recovery_failures_total.labels(effect=effect, phase=phase)


@_optional_observation
def record_recovery_failure(effect: str, phase: RecoveryPhase) -> None:
    _due_recovery_failures_total.labels(effect=effect, phase=phase).inc()


@_optional_observation
def record_recovery_pass(effect: str, outcome: RecoveryOutcome) -> None:
    _due_recovery_passes_total.labels(effect=effect, outcome=outcome).inc()


@_optional_observation
def record_outstanding(effect: str, count: int) -> None:
    _due_outstanding.labels(effect=effect).set(count)


@_optional_observation
def record_work_operation(
    effect: str,
    operation: DurableWorkOperation,
    *,
    count: int = 1,
) -> None:
    _due_operations_total.labels(**DurableWorkLabels(effect=effect, operation=operation)).inc(count)


@_optional_observation
def record_work_census(
    effect: str,
    state: DurableWorkState,
    *,
    count: int,
    oldest_age_seconds: float,
) -> None:
    labels = DurableWorkStateLabels(effect=effect, state=state)
    _due_backlog.labels(**labels).set(count)
    _due_oldest_age_seconds.labels(**labels).set(oldest_age_seconds)


_due_repair_expired_total = Counter(
    "due_repair_expired_total",
    "Unknown attempts whose repair window expired by product policy, without a provider receipt.",
    labelnames=("effect",),
)


@_optional_observation
def record_repair_expired(effect: str) -> None:
    _due_repair_expired_total.labels(effect=effect).inc()


_due_eligible = Gauge(
    "due_eligible",
    "Time-due work eligible for execution, for capabilities with an eligibility gate.",
    labelnames=("effect",),
    multiprocess_mode="livemostrecent",
)
_due_waiting_eligibility = Gauge(
    "due_waiting_eligibility",
    "Time-due obligations retained while their execution prerequisite is false.",
    labelnames=("effect",),
    multiprocess_mode="livemostrecent",
)


@_optional_observation
def record_execution_eligibility(effect: str, *, eligible: int, waiting: int) -> None:
    _due_eligible.labels(effect=effect).set(eligible)
    _due_waiting_eligibility.labels(effect=effect).set(waiting)


@_optional_observation
def record_recovery_unclean(effect: str, timestamp: float) -> None:
    """Publish an unclean tick's end time without changing durable work truth."""
    _due_last_unclean_recovery.labels(effect=effect).set(timestamp)
