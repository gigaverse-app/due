"""
RecoveryPass decides which failures cost one identity and which end the tick.

Design: docs/architecture.md.
"""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from prometheus_client import REGISTRY
from sentry_sdk.types import Event

from due._installed import NotInstalledError
from due.django import integration
from due.django.integration import ApplicationHooks
from due.django.runtime import metrics, recovery_pass
from due.django.runtime.metrics import RecoveryOutcome, RecoveryPhase
from due.django.runtime.recovery_pass import (
    DEFAULT_STALE_AFTER,
    MAX_REPORTED_FAILURES,
    RecoveryIncomplete,
    RecoveryPass,
    group_recovery_reports,
    recovery_pass_for,
    recovery_tick,
)
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

PHASE = RecoveryPhase.DISPATCH


class LockUnavailable(RuntimeError):
    """Stands in for a helper that wraps whatever interrupted it, as advisory locks do."""


class SessionLost(RuntimeError):
    pass


def _wrapped(cause: BaseException) -> LockUnavailable:
    try:
        raise cause
    except BaseException as error:  # noqa: BLE001 - building a chained exception
        wrapper = LockUnavailable("could not acquire advisory lock")
        wrapper.__cause__ = error
        return wrapper


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(SoftTimeLimitExceeded(), id="soft-limit"),
        pytest.param(_wrapped(SoftTimeLimitExceeded()), id="soft-limit-wrapped-by-a-lock-helper"),
        pytest.param(NotInstalledError("slot"), id="unwired-slot"),
        pytest.param(_wrapped(NotInstalledError("slot")), id="unwired-slot-wrapped"),
    ],
)
def test_a_tick_ending_failure_is_recognised_anywhere_in_the_cause_chain(failure: BaseException) -> None:
    tick = RecoveryPass("tests.effect")
    reached: list[int] = []
    with pytest.raises(type(failure)):
        for identity in (1, 2):
            with tick.isolate(PHASE, identity):
                reached.append(identity)
                raise failure
    assert reached == [1], "the tick stopped instead of moving on to the next identity"
    assert not tick.errors


def test_a_declared_propagate_type_ends_the_tick_and_others_are_isolated() -> None:
    tick = RecoveryPass("tests.effect", propagate=(SessionLost,))
    with tick.isolate(PHASE, 1):
        raise ValueError("one bad row")
    assert len(tick.errors) == 1
    lost = LockUnavailable("release failed")
    lost.__cause__ = SessionLost("connection closed")
    with pytest.raises(LockUnavailable), tick.isolate(PHASE, 2):
        raise lost


def test_the_raised_group_keeps_the_first_failures_and_the_true_count() -> None:
    tick = RecoveryPass("tests.effect")
    for identity in range(MAX_REPORTED_FAILURES + 5):
        with tick.isolate(PHASE, identity):
            raise OSError(f"broker down for {identity}")
    with pytest.raises(RecoveryIncomplete) as raised:
        tick.raise_if_failed()
    assert len(raised.value.exceptions) == MAX_REPORTED_FAILURES
    assert f"{MAX_REPORTED_FAILURES + 5} failure(s)" in str(raised.value)


def test_a_standalone_pass_counts_its_outcome_before_raising() -> None:
    outcomes: list[RecoveryOutcome] = []
    with (
        patch(
            "due.django.runtime.recovery_pass.record_recovery_pass",
            lambda effect, outcome: outcomes.append(outcome),
        ),
        pytest.raises(RecoveryIncomplete),
        recovery_pass_for("tests.effect", None) as tick,
        tick.isolate(PHASE, 1),
    ):
        raise OSError("lost acknowledgement")
    assert outcomes == [RecoveryOutcome.FAILED]


def test_a_soft_limit_while_counting_an_isolated_failure_ends_the_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    # Metric failures are swallowed so telemetry never changes product outcomes; Celery's stop request is not one.
    class StopsWhileCounting:
        def labels(self, **labels: object) -> StopsWhileCounting:
            return self

        def inc(self) -> None:
            raise SoftTimeLimitExceeded()

    monkeypatch.setattr(metrics, "_due_recovery_failures_total", StopsWhileCounting())
    tick = RecoveryPass("tests.effect")
    reached: list[int] = []
    with pytest.raises(SoftTimeLimitExceeded):
        for identity in (1, 2):
            with tick.isolate(PHASE, identity):
                reached.append(identity)
                raise ValueError("one bad row")
    assert reached == [1]


@pytest.fixture(name="reported")
def reported_fixture(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[object]]:
    reported: dict[str, list[object]] = {"outcomes": [], "heartbeats": [], "unclean": []}
    monkeypatch.setattr(
        recovery_pass, "record_recovery_pass", lambda effect, outcome: reported["outcomes"].append(outcome)
    )
    monkeypatch.setattr(
        recovery_pass, "record_recovery_completed", lambda effect, at: reported["heartbeats"].append(at)
    )
    monkeypatch.setattr(recovery_pass, "record_recovery_unclean", lambda effect, at: reported["unclean"].append(effect))
    monkeypatch.setattr(recovery_pass, "_coordination_now", lambda: datetime(2026, 10, 10, tzinfo=UTC))
    return reported


def test_a_clean_tick_counts_clean_and_advances_the_heartbeat(reported: dict[str, list[object]]) -> None:
    with recovery_tick("tests.effect") as tick, tick.isolate(PHASE, 1):
        pass
    assert reported == {
        "outcomes": [RecoveryOutcome.CLEAN],
        "heartbeats": [datetime(2026, 10, 10, tzinfo=UTC).timestamp()],
        "unclean": [],
    }


def test_a_tick_that_isolated_a_failure_counts_failed_and_leaves_the_heartbeat(
    reported: dict[str, list[object]],
) -> None:
    with pytest.raises(RecoveryIncomplete) as raised, recovery_tick("tests.effect") as tick, tick.isolate(PHASE, 1):
        raise OSError("broker refused this publication")
    assert raised.value.effect == "tests.effect"
    assert reported == {"outcomes": [RecoveryOutcome.FAILED], "heartbeats": [], "unclean": ["tests.effect"]}


def test_a_tick_ended_early_counts_aborted(reported: dict[str, list[object]]) -> None:
    with pytest.raises(SoftTimeLimitExceeded), recovery_tick("tests.effect") as tick, tick.isolate(PHASE, 1):
        raise SoftTimeLimitExceeded()
    assert reported == {"outcomes": [RecoveryOutcome.ABORTED], "heartbeats": [], "unclean": ["tests.effect"]}


def test_a_standalone_loop_reports_its_outcome_without_claiming_a_tick(reported: dict[str, list[object]]) -> None:
    with recovery_pass_for("tests.effect", None) as tick, tick.isolate(PHASE, 1):
        pass
    assert reported == {"outcomes": [RecoveryOutcome.CLEAN], "heartbeats": [], "unclean": []}


def test_sentry_groups_incomplete_ticks_by_effect_not_by_leaf_exception() -> None:
    def event_for(effect: str) -> Event:
        try:
            with recovery_tick(effect, heartbeat=False) as tick, tick.isolate(PHASE, 1):
                raise ValueError("source_key: This field cannot be blank.")
        except RecoveryIncomplete as incomplete:
            return group_recovery_reports({"tags": {}}, {"exc_info": (type(incomplete), incomplete, None)})
        raise AssertionError("the tick did not raise")

    with patch.object(recovery_pass, "record_recovery_pass", lambda effect, outcome: None):
        pictures, backgrounds = event_for("core.picturework"), event_for("core.backgroundwork")
    assert pictures["fingerprint"] == ["durable-work-recovery-incomplete", "core.picturework", "{{ default }}"]
    assert backgrounds["fingerprint"] == ["durable-work-recovery-incomplete", "core.backgroundwork", "{{ default }}"]
    assert pictures["tags"] == {"durable_work.effect": "core.picturework"}
    unrelated = {"tags": {}}
    assert group_recovery_reports(unrelated, {"exc_info": (ValueError, ValueError(), None)}) == {"tags": {}}


def test_a_tick_creates_its_counters_at_zero_so_a_first_failure_is_an_increase() -> None:
    # A series that first appears at 1 has no increase(); alerts would miss a process's first failure.
    effect = "tests.counted_effect"
    with recovery_tick(effect, heartbeat=False) as tick, tick.isolate(PHASE, 1):
        pass
    for outcome in RecoveryOutcome:
        sample = REGISTRY.get_sample_value("due_recovery_passes_total", {"effect": effect, "outcome": outcome.value})
        assert sample is not None, f"no {outcome.value} series before any such tick"
    assert REGISTRY.get_sample_value("due_recovery_failures_total", {"effect": effect, "phase": PHASE.value}) == 0


@pytest.mark.django_db
def test_a_heartbeat_tick_publishes_its_stale_threshold_before_its_first_clean_tick() -> None:
    # The stale rule compares the heartbeat's age with this, so it must exist even when every tick fails.
    with pytest.raises(RecoveryIncomplete), recovery_tick("tests.daily_effect", stale_after=timedelta(days=2)) as tick:
        with tick.isolate(PHASE, 1):
            raise ValueError("one bad row")
    assert (
        REGISTRY.get_sample_value("due_recovery_stale_after_seconds", {"effect": "tests.daily_effect"})
        == timedelta(days=2).total_seconds()
    )
    with recovery_tick("tests.minute_effect"):
        pass
    assert (
        REGISTRY.get_sample_value("due_recovery_stale_after_seconds", {"effect": "tests.minute_effect"})
        == DEFAULT_STALE_AFTER.total_seconds()
    )


def test_a_standalone_loop_publishes_no_stale_threshold() -> None:
    # A fast-path hint sharing an effect with its sweep must not overwrite the sweep's threshold.
    with recovery_tick("tests.hinted_effect", heartbeat=False, stale_after=timedelta(seconds=5)):
        pass
    assert REGISTRY.get_sample_value("due_recovery_stale_after_seconds", {"effect": "tests.hinted_effect"}) is None


@pytest.mark.parametrize("wrapped", [False, True], ids=["direct", "wrapped"])
def test_application_fatal_error_aborts_without_a_row_deferral_or_clean_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    reported: dict[str, list[object]],
    wrapped: bool,
) -> None:
    # An application exception is configured by type; names and Due's slot class are irrelevant.
    monkeypatch.setattr(integration, "_hooks", ApplicationHooks())
    integration.configure(ApplicationHooks(abort_recovery_on=(SessionLost,)))
    failure = _wrapped(SessionLost("application service unavailable")) if wrapped else SessionLost("unavailable")
    reached: list[int] = []
    with pytest.raises(type(failure)), recovery_tick("tests.application_wiring") as tick:
        for identity in (1, 2):
            with tick.isolate(PHASE, identity):
                reached.append(identity)
                raise failure
    assert reached == [1]
    assert not tick.errors
    assert reported == {
        "outcomes": [RecoveryOutcome.ABORTED],
        "heartbeats": [],
        "unclean": ["tests.application_wiring"],
    }


def test_a_failing_standalone_loop_stamps_no_unclean_tick(reported: dict[str, list[object]]) -> None:
    # Only a heartbeat tick is the effect's latest tick; a fast-path hint failing must not page a slow sweep.
    with pytest.raises(RecoveryIncomplete), recovery_pass_for("tests.effect", None) as tick, tick.isolate(PHASE, 1):
        raise OSError("lost acknowledgement")
    assert reported == {"outcomes": [RecoveryOutcome.FAILED], "heartbeats": [], "unclean": []}


def test_an_unclean_heartbeat_tick_stamps_the_time_the_slow_sweep_rule_compares_with_the_heartbeat() -> None:
    """
    INVARIANT: a failed or aborted heartbeat tick records when it ended; a clean one does not touch it.

    The slow-sweep alert pages while this stamp is newer than the last clean
    tick (docs/technical-design/durable-work-library.md#recovery-alerts). It is
    the worker's wall clock, so an aborted tick never reads a database that may
    be what aborted it.
    """

    def stamp(effect: str) -> float | None:
        return REGISTRY.get_sample_value("due_last_unclean_recovery_timestamp_seconds", {"effect": effect})

    before = time.time()
    with pytest.raises(RecoveryIncomplete), recovery_tick("tests.daily_failed", stale_after=timedelta(days=2)) as tick:
        with tick.isolate(PHASE, 1):
            raise ValueError("one bad row")
    failed_at = stamp("tests.daily_failed")
    assert failed_at is not None and before <= failed_at <= time.time()

    # A separate effect, so the aborted tick's own stamp is what is read.
    with pytest.raises(SoftTimeLimitExceeded), recovery_tick("tests.daily_aborted", stale_after=timedelta(days=2)):
        raise SoftTimeLimitExceeded()
    aborted_at = stamp("tests.daily_aborted")
    assert aborted_at is not None and failed_at <= aborted_at <= time.time()

    with patch.object(recovery_pass, "_coordination_now", lambda: datetime.now(UTC)):
        with recovery_tick("tests.daily_aborted", stale_after=timedelta(days=2)):
            pass
        with recovery_tick("tests.daily_clean", stale_after=timedelta(days=2)):
            pass
    assert stamp("tests.daily_aborted") == aborted_at, "a clean tick moved the unclean stamp"
    assert stamp("tests.daily_clean") is None, "a clean tick stamped an unclean one"


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
def test_provider_wait_is_durable_paced_and_not_a_recovery_failure():
    from due.django.models import RecoveryDeferral
    from due.django.public import database_now
    from due.django.testing import use_database_clock
    from due.errors import WorkDeferredError
    from tests.harness.primitive import PrimitiveOwner

    PrimitiveOwner.objects.create(pk=1)
    now = database_now()
    for _ in range(7):
        with use_database_clock(lambda now=now: now), recovery_tick("tests.wait") as tick:
            with tick.isolate(PHASE, 1):
                raise WorkDeferredError("HTTP 503", retry_after=timedelta(minutes=2))
            assert tick.held(PHASE, PrimitiveOwner.objects.values_list("pk", flat=True)) == {"1"}
        hold = RecoveryDeferral.objects.get(effect="tests.wait", identity="1")
        assert hold.failures == 0
        assert hold.retry_at == now + timedelta(minutes=2)
        assert tick.failures == 0
        now = hold.retry_at
    with use_database_clock(lambda now=now: now), recovery_tick("tests.wait") as tick:
        tick.held(PHASE, PrimitiveOwner.objects.values_list("pk", flat=True))
        with tick.isolate(PHASE, 1):
            pass
    assert not RecoveryDeferral.objects.filter(effect="tests.wait").exists()


@pytest.mark.django_db(transaction=True)
def test_a_long_provider_wait_survives_census_retention_and_shorter_reports():
    from due.django.models import RecoveryDeferral
    from due.django.public import database_now
    from due.django.testing import use_database_clock
    from due.errors import WorkDeferredError

    now = database_now()
    with use_database_clock(lambda: now):
        recovery_pass.defer_identity(
            "tests.long-wait",
            PHASE,
            "1",
            "waiting",
            wait=WorkDeferredError("unavailable", retry_after=timedelta(days=10)),
        )
        recovery_pass.defer_identity(
            "tests.long-wait",
            PHASE,
            "1",
            "waiting",
            wait=WorkDeferredError("unavailable", retry_after=timedelta(seconds=30)),
        )
    recovery_pass.publish_recovery_deferrals("tests.long-wait", observed_at=now + timedelta(days=8))
    hold = RecoveryDeferral.objects.get(effect="tests.long-wait")
    assert hold.retry_at == now + timedelta(days=10)
    assert hold.failures == 0
    labels = {"effect": "tests.long-wait", "phase": PHASE.value}
    assert REGISTRY.get_sample_value(f"{metrics.METRIC_NAMESPACE}_recovery_waiting", labels) == 1
    assert REGISTRY.get_sample_value(f"{metrics.METRIC_NAMESPACE}_recovery_deferred", labels) == 0
