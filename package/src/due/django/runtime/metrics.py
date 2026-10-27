"""Shared observations of backlog, attempts, state and recovery.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from collections.abc import Callable, Mapping
from enum import Enum, StrEnum
from functools import wraps
from types import MappingProxyType
from typing import TypedDict

from celery.exceptions import SoftTimeLimitExceeded
from prometheus_client import Counter, Gauge

from due.django.models import DurableWorkState, EffectAttemptOutcome
from due.django.runtime.names import METRIC_NAMESPACE
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


type MetricLabels = Mapping[str, type[str] | type[Enum]]
_METRIC_SCHEMAS: dict[str, MetricLabels] = {}


def metric_schemas() -> Mapping[str, MetricLabels]:
    """Read-only names, labels and enum vocabularies actually declared by Due.

    Applications can validate dashboards and alerts without re-declaring the
    metrics or inspecting prometheus_client's private collector attributes.
    Names reflect ``DUE_METRIC_NAMESPACE`` configured before Django starts.
    """
    return MappingProxyType(_METRIC_SCHEMAS)


def _counter(name: str, description: str, *, labels: MetricLabels) -> Counter:
    _METRIC_SCHEMAS[name] = MappingProxyType(dict(labels))
    return Counter(name, description, labelnames=tuple(labels))


def _gauge(name: str, description: str, *, labels: MetricLabels) -> Gauge:
    _METRIC_SCHEMAS[name] = MappingProxyType(dict(labels))
    return Gauge(name, description, labelnames=tuple(labels), multiprocess_mode="livemostrecent")


# `effect` comes only from registered Django model labels. It is finite code
# configuration, never work/provider/user data, so the series set stays bounded
# by the number of adopters.
_due_operations_total = _counter(
    f"{METRIC_NAMESPACE}_operations_total",
    "Durable-effect coordination operations by registered work type and outcome.",
    labels={"effect": str, "operation": DurableWorkOperation},
)
_due_backlog = _gauge(
    f"{METRIC_NAMESPACE}_backlog",
    "Active and failed durable effects by work type and state; retained successes are excluded.",
    labels={"effect": str, "state": DurableWorkState},
)
_due_oldest_age_seconds = _gauge(
    f"{METRIC_NAMESPACE}_oldest_age_seconds",
    "Age of the oldest active or failed durable effect by work type and state; successes are excluded.",
    labels={"effect": str, "state": DurableWorkState},
)

_due_outstanding = _gauge(
    f"{METRIC_NAMESPACE}_outstanding",
    "All active durable obligations, including queued and delayed retries.",
    labels={"effect": str},
)

_due_last_recovery = _gauge(
    f"{METRIC_NAMESPACE}_last_recovery_timestamp_seconds",
    "Last completed recovery tick; distinguishes an idle effect from a stalled scheduler.",
    labels={"effect": str},
)

_due_last_unclean_recovery = _gauge(
    f"{METRIC_NAMESPACE}_last_unclean_recovery_timestamp_seconds",
    "Last failed or aborted recovery tick; newer than the clean heartbeat means recovery is still unclean.",
    labels={"effect": str},
)

_due_recovery_stale_after = _gauge(
    f"{METRIC_NAMESPACE}_recovery_stale_after_seconds",
    "How long an effect may go without a clean recovery tick before it is stale: twice its slowest cadence.",
    labels={"effect": str},
)

_due_pending_attempts = _gauge(
    f"{METRIC_NAMESPACE}_pending_provider_attempts",
    "Provider attempts whose possible or known writes still require reconciliation.",
    labels={"effect": str, "outcome": EffectAttemptOutcome},
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


_due_recovery_failures_total = _counter(
    f"{METRIC_NAMESPACE}_recovery_failures_total",
    "Identities (or whole phases) a recovery tick isolated after their step raised; each also fails the tick.",
    labels={"effect": str, "phase": RecoveryPhase},
)
_due_recovery_passes_total = _counter(
    f"{METRIC_NAMESPACE}_recovery_passes_total",
    "Recovery ticks by outcome; a failed tick still ran every phase, an aborted one stopped early.",
    labels={"effect": str, "outcome": RecoveryOutcome},
)


_due_recovery_deferred = _gauge(
    f"{METRIC_NAMESPACE}_recovery_deferred",
    "Identities a recovery phase isolated and is holding out with backoff; each is still owed.",
    labels={"effect": str, "phase": RecoveryPhase},
)
_due_recovery_deferred_oldest_age_seconds = _gauge(
    f"{METRIC_NAMESPACE}_recovery_deferred_oldest_age_seconds",
    "Age of the oldest first failure among an effect's deferred identities, per phase.",
    labels={"effect": str, "phase": RecoveryPhase},
)


_due_recovery_waiting = _gauge(
    f"{METRIC_NAMESPACE}_recovery_waiting",
    "Owed identities waiting for a provider, without spending failure budget.",
    labels={"effect": str, "phase": RecoveryPhase},
)
_due_recovery_waiting_oldest_age_seconds = _gauge(
    f"{METRIC_NAMESPACE}_recovery_waiting_oldest_age_seconds",
    "Age of the oldest retained provider wait, per phase.",
    labels={"effect": str, "phase": RecoveryPhase},
)


@_optional_observation
def record_recovery_waits(effect: str, phase: RecoveryPhase, *, count: int, oldest_age_seconds: float) -> None:
    _due_recovery_waiting.labels(effect=effect, phase=phase).set(count)
    _due_recovery_waiting_oldest_age_seconds.labels(effect=effect, phase=phase).set(oldest_age_seconds)


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


_due_repair_expired_total = _counter(
    f"{METRIC_NAMESPACE}_repair_expired_total",
    "Unknown attempts whose repair window expired by product policy, without a provider receipt.",
    labels={"effect": str},
)


@_optional_observation
def record_repair_expired(effect: str) -> None:
    _due_repair_expired_total.labels(effect=effect).inc()


_due_repair_retired_total = _counter(
    f"{METRIC_NAMESPACE}_repair_retired_total",
    "Older-revision repair debt settled because the provider confirmed its target is permanently gone.",
    labels={"effect": str},
)


@_optional_observation
def record_repair_retired(effect: str, count: int) -> None:
    _due_repair_retired_total.labels(effect=effect).inc(count)


_due_eligible = _gauge(
    f"{METRIC_NAMESPACE}_eligible",
    "Time-due work eligible for execution, for capabilities with an eligibility gate.",
    labels={"effect": str},
)
_due_waiting_eligibility = _gauge(
    f"{METRIC_NAMESPACE}_waiting_eligibility",
    "Time-due obligations retained while their execution prerequisite is false.",
    labels={"effect": str},
)


@_optional_observation
def record_execution_eligibility(effect: str, *, eligible: int, waiting: int) -> None:
    _due_eligible.labels(effect=effect).set(eligible)
    _due_waiting_eligibility.labels(effect=effect).set(waiting)


@_optional_observation
def record_recovery_unclean(effect: str, timestamp: float) -> None:
    """Publish an unclean tick's end time without changing durable work truth."""
    _due_last_unclean_recovery.labels(effect=effect).set(timestamp)
