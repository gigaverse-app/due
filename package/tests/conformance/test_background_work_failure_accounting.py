"""
A worker failure is an observation the test must account for, never a silent pass.

A golden can reach its final product state through a second delivery while
the first delivery crashed on a genuine programming error. ``BackgroundWork``
records every failure; these controls prove that an unaccounted one fails the
test, that an injected fault claimed by type passes, and that a wrong or extra
failure during a fault scenario still fails.

A Celery retry is the same observation, deferred. Executed eagerly,
``Task.retry`` raises ``Retry`` without publishing anything (a worker would
publish ``retry.sig``), so the broker publishes it: the retry is held to its
ETA and delivered like any message, a retry that later succeeds passes, a
task that keeps failing becomes a failure once its own ``max_retries`` is
spent, and a retry the test never lets run is a failure the test must claim.
"""

import random
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import time_machine
from celery import Task
from celery import current_app as celery_app
from celery.exceptions import MaxRetriesExceededError, Retry
from celery.utils.time import get_exponential_backoff_interval
from django.utils import timezone

from due.django.conformance.background import BackgroundWork, accounted_background_work


class InjectedFault(RuntimeError):
    """The fault a scenario deliberately injects."""


class ProgrammingError(RuntimeError):
    """A crash nobody intended — the failure these controls must surface."""


_SCRIPTED = InjectedFault("scripted")


@pytest.fixture
def failing_task() -> Iterator[Task]:
    def fail(kind: str) -> None:
        if kind == "scripted":
            raise _SCRIPTED  # a fake re-raising one scripted error instance
        raise {"injected": InjectedFault, "bug": ProgrammingError}[kind](kind)

    task = celery_app.task(name="tests.background_work.failing", lazy=False)(fail)
    try:
        yield task
    finally:
        celery_app.tasks.pop(task.name)


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch) -> BackgroundWork:
    return BackgroundWork().install(monkeypatch)


@pytest.mark.no_db
def test_an_unaccounted_worker_failure_fails_even_after_a_later_success(
    driver: BackgroundWork, failing_task: Task
) -> None:
    failing_task.delay("bug")
    # The product state could be reached by a later delivery; the crash still counts.
    with pytest.raises(AssertionError, match="ProgrammingError"):
        driver.assert_no_unexpected_failures()


@pytest.mark.no_db
def test_a_claimed_injected_fault_passes(driver: BackgroundWork, failing_task: Task) -> None:
    failing_task.delay("injected")
    claimed = driver.expect_failures(InjectedFault)
    assert [type(failure.error) for failure in claimed] == [InjectedFault]
    driver.assert_no_unexpected_failures()


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("deliveries", "expected"),
    [
        (("bug",), (InjectedFault,)),
        (("injected", "bug"), (InjectedFault,)),
        ((), (InjectedFault,)),
    ],
    ids=["wrong-type", "extra-failure", "missing-failure"],
)
def test_a_fault_scenario_rejects_wrong_extra_or_missing_failures(
    driver: BackgroundWork, failing_task: Task, deliveries: tuple[str, ...], expected: tuple[type[Exception], ...]
) -> None:
    for kind in deliveries:
        failing_task.delay(kind)
    with pytest.raises(AssertionError, match="expected worker failures"):
        driver.expect_failures(*expected)


@pytest.mark.no_db
def test_the_accounted_scope_fails_at_exit_on_an_unclaimed_crash(
    monkeypatch: pytest.MonkeyPatch, failing_task: Task
) -> None:
    # The scope the shared ``background_work`` fixture wraps every golden in.
    with pytest.raises(AssertionError, match="never accounted for"):
        with accounted_background_work(monkeypatch):
            failing_task.delay("bug")


@pytest.mark.no_db
def test_the_accounted_scope_passes_a_claimed_fault(monkeypatch: pytest.MonkeyPatch, failing_task: Task) -> None:
    with accounted_background_work(monkeypatch) as driver:
        failing_task.delay("injected")
        driver.expect_failures(InjectedFault)


@pytest.mark.no_db
def test_two_equal_failures_are_two_failures(driver: BackgroundWork, failing_task: Task) -> None:
    # Same task, arguments and re-raised exception instance: equal by value, but
    # claiming the first must not account for the second.
    failing_task.delay("scripted")
    driver.expect_failures(InjectedFault)
    failing_task.delay("scripted")
    with pytest.raises(AssertionError, match="never accounted for"):
        driver.assert_no_unexpected_failures()


@pytest.mark.no_db
@pytest.mark.parametrize("broad", [RuntimeError, Exception, BaseException])
def test_a_base_class_claim_does_not_absorb_a_subclass(
    driver: BackgroundWork, failing_task: Task, broad: type[BaseException]
) -> None:
    # ProgrammingError subclasses RuntimeError; claiming a base must not absorb the crash.
    failing_task.delay("bug")
    with pytest.raises(AssertionError, match="expected worker failures"):
        driver.expect_failures(broad)


class TransientFault(RuntimeError):
    """A fault the task declares retryable with ``autoretry_for``."""


@pytest.fixture
def register_task() -> Iterator[Callable[..., Task]]:
    """Register throwaway tasks with Celery's real retry options; unregister them afterwards."""
    names: list[str] = []

    def register(run: Callable[..., None], name: str, **options: Any) -> Task:
        task = celery_app.task(name=f"tests.background_work.{name}", lazy=False, **options)(run)
        names.append(task.name)
        return task

    try:
        yield register
    finally:
        for name in names:
            celery_app.tasks.pop(name)


@pytest.fixture
def clock() -> Iterator[time_machine.Traveller]:
    with time_machine.travel(datetime(2026, 1, 5, 12, tzinfo=UTC), tick=False) as traveller:
        yield traveller


@pytest.fixture
def clocked(monkeypatch: pytest.MonkeyPatch, clock: time_machine.Traveller) -> BackgroundWork:
    return BackgroundWork(clock=clock).install(monkeypatch)


def _autoretrying(
    register_task: Callable[..., Task], name: str, *, fail_times: int, countdown: int, max_retries: int | None
) -> tuple[Task, list[int]]:
    """
    A task raising ``TransientFault`` on its first ``fail_times`` runs; returns it and the ``retries`` of each run.

    ``max_retries`` is the task's own option (``None`` retries forever); a
    ``None`` passed to ``retry`` would mean "the task's default" instead.
    """
    runs: list[int] = []

    def run(self: Task) -> None:
        runs.append(self.request.retries)
        if len(runs) <= fail_times:
            raise TransientFault(f"run {len(runs)}")

    task = register_task(
        run,
        name,
        bind=True,
        autoretry_for=(TransientFault,),
        retry_backoff=False,
        retry_kwargs={"countdown": countdown},
        max_retries=max_retries,
    )
    return task, runs


@pytest.mark.no_db
def test_an_autoretried_fault_is_redelivered_at_its_eta_and_the_success_passes(
    clocked: BackgroundWork, register_task: Callable[..., Task]
) -> None:
    task, runs = _autoretrying(register_task, "flaky", fail_times=1, countdown=30, max_retries=3)
    task.delay()
    [first] = clocked.executed
    assert isinstance(first.retry, Retry), "the delivery that asked to retry still records its Retry"
    # The retry is a scheduled message, published with the next retry count.
    [retry] = clocked.held
    assert first.retried_as is retry
    assert (retry.retries, retry.eta) == (1, timezone.now() + timedelta(seconds=30))
    assert clocked.advance(29) == [] and runs == [0]
    assert clocked.advance(1) == [retry] and runs == [0, 1]
    assert clocked.held == [] and clocked.failures == []
    clocked.assert_no_unexpected_failures()


@pytest.mark.no_db
@pytest.mark.parametrize("max_retries", [0, 2])
def test_a_task_that_always_fails_is_a_failure_once_its_own_max_retries_is_spent(
    clocked: BackgroundWork, register_task: Callable[..., Task], max_retries: int
) -> None:
    task, runs = _autoretrying(register_task, "broken", fail_times=99, countdown=10, max_retries=max_retries)
    task.delay()
    # Bounded: one advance past each retry's ETA, and one more that must deliver nothing.
    for _ in range(max_retries + 1):
        clocked.advance(10)
    assert runs == list(range(max_retries + 1))
    [failure] = clocked.failures
    assert type(failure.error) is TransientFault and str(failure.error) == f"run {max_retries + 1}"
    assert failure.retries == max_retries and clocked.held == []
    with pytest.raises(AssertionError, match="never accounted for.*TransientFault"):
        clocked.assert_no_unexpected_failures()
    clocked.expect_failures(TransientFault)
    clocked.assert_no_unexpected_failures()


@pytest.mark.no_db
def test_a_bare_retry_that_runs_out_fails_with_max_retries_exceeded(
    clocked: BackgroundWork, register_task: Callable[..., Task]
) -> None:
    runs: list[int] = []

    def poll(self: Task) -> None:
        runs.append(self.request.retries)
        raise self.retry(countdown=5, max_retries=1)

    task = register_task(poll, "poll", bind=True)
    task.delay()
    clocked.advance(5)
    assert runs == [0, 1]
    clocked.expect_failures(MaxRetriesExceededError)
    assert clocked.held == []


@pytest.mark.no_db
def test_the_retry_runs_with_the_arguments_the_task_retried_with(
    clocked: BackgroundWork, register_task: Callable[..., Task]
) -> None:
    seen: list[tuple[int, list[str] | None]] = []

    def resume(self: Task, item: int, progress: list[str] | None = None) -> None:
        seen.append((item, progress))
        if progress is None:
            raise self.retry(kwargs={"progress": ["half"]}, countdown=0)

    task = register_task(resume, "resume", bind=True)
    task.delay(7)
    # A zero countdown is due at once, but it is still a published message: the next drain delivers it.
    assert seen == [(7, None)]
    clocked.drain()
    assert seen == [(7, None), (7, ["half"])]
    clocked.assert_no_unexpected_failures()


@pytest.mark.no_db
def test_a_retry_the_test_never_lets_run_is_an_unaccounted_failure(
    monkeypatch: pytest.MonkeyPatch, clock: time_machine.Traveller, register_task: Callable[..., Task]
) -> None:
    # R4's probe: an always-failing autoretry task, delivered and drained, time never advanced.
    task, runs = _autoretrying(register_task, "stalled", fail_times=99, countdown=180, max_retries=3)
    with pytest.raises(AssertionError, match="never accounted for.*TransientFault"):
        with accounted_background_work(monkeypatch, clock) as driver:
            task.delay()
            driver.drain()
    assert runs == [0]


@pytest.mark.no_db
def test_a_scheduled_retry_is_claimed_like_any_injected_fault(
    monkeypatch: pytest.MonkeyPatch, clock: time_machine.Traveller, register_task: Callable[..., Task]
) -> None:
    task, _runs = _autoretrying(register_task, "claimed", fail_times=99, countdown=180, max_retries=3)
    with accounted_background_work(monkeypatch, clock) as driver:
        task.delay()
        [claimed] = driver.expect_failures(TransientFault)
        assert claimed.retried_as in driver.held


@pytest.mark.no_db
def test_a_redelivered_copy_does_not_inherit_the_original_retry(
    clocked: BackgroundWork, register_task: Callable[..., Task]
) -> None:
    task, runs = _autoretrying(register_task, "redelivered", fail_times=1, countdown=30, max_retries=3)
    task.delay()
    [original] = clocked.executed
    # A duplicate of the first message succeeds; only the original still has a retry pending.
    clocked.redeliver(original)
    assert runs == [0, 0]
    assert clocked.unexpected_failures() == [original]


@pytest.mark.no_db
def test_a_retry_with_no_delay_takes_one_attempt_per_drain(
    clocked: BackgroundWork, register_task: Callable[..., Task]
) -> None:
    # max_retries=None with a zero countdown retries forever; here it gives up after 20 failures, so a
    # drain that keeps delivering what it republishes returns (with 21 runs) instead of hanging.
    task, runs = _autoretrying(register_task, "relentless", fail_times=20, countdown=0, max_retries=None)
    task.delay()
    assert runs == [0]
    [first_retry] = clocked.held
    assert clocked.drain() == [first_retry]
    assert runs == [0, 1]
    # The retry that delivery published is due now, but waits for the next drain call.
    [second_retry] = clocked.held
    assert second_retry.retries == 2 and second_retry.eta == timezone.now()
    assert clocked.drain() == [second_retry]
    assert runs == [0, 1, 2]


@pytest.mark.no_db
@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("shape", ["autoretry_for", "explicit_backoff"])
def test_a_jittered_backoff_is_deterministic_under_the_broker(
    clocked: BackgroundWork, register_task: Callable[..., Task], shape: str, seed: int
) -> None:
    # Full jitter draws the first countdown from {0, 1}: unpatched, some seeds make the retry due on a
    # drain and others leave it pending, so a test that never advances time passed or failed by chance.
    random.seed(seed)
    if shape == "autoretry_for":
        task, _runs = _autoretrying_with_backoff(register_task, f"jittered_{seed}")
    else:

        def explicit(self: Task) -> None:
            # The live-room finalizer's shape: production computes the jittered countdown itself.
            countdown = get_exponential_backoff_interval(
                factor=1, retries=self.request.retries, maximum=60, full_jitter=True
            )
            raise self.retry(exc=TransientFault("explicit"), countdown=countdown)

        task = register_task(explicit, f"explicit_jitter_{seed}", bind=True)
    task.delay()
    [retry] = clocked.held
    # Jitter draws its upper bound: the un-jittered backoff, factor * 2 ** retries = 1 s on the first retry.
    assert retry.eta == timezone.now() + timedelta(seconds=1)
    assert clocked.drain() == []


def _autoretrying_with_backoff(register_task: Callable[..., Task], name: str) -> tuple[Task, list[int]]:
    runs: list[int] = []

    def run(self: Task) -> None:
        runs.append(self.request.retries)
        raise TransientFault(f"run {len(runs)}")

    task = register_task(run, name, bind=True, autoretry_for=(TransientFault,), retry_backoff=True, max_retries=3)
    return task, runs


@pytest.mark.no_db
def test_a_dropped_retry_is_a_lost_message_the_test_must_still_claim(
    clocked: BackgroundWork, register_task: Callable[..., Task]
) -> None:
    task, _runs = _autoretrying(register_task, "dropped", fail_times=99, countdown=30, max_retries=3)
    task.delay()
    [original] = clocked.executed
    assert clocked.drop_held(task) == [original.retried_as]
    # Losing the retry does not undo the raise: nothing will run that message again.
    with pytest.raises(AssertionError, match=r"TransientFault.*retry 1 lost before delivery") as raised:
        clocked.assert_no_unexpected_failures()
    assert "held until" not in str(raised.value)
    clocked.expect_failures(TransientFault)
    clocked.assert_no_unexpected_failures()


@pytest.mark.no_db
def test_a_retry_keeps_the_publication_options_a_worker_keeps(
    clocked: BackgroundWork, register_task: Callable[..., Task]
) -> None:
    task, _runs = _autoretrying(register_task, "routed", fail_times=1, countdown=30, max_retries=3)
    kept = {"queue": "retries-q", "expires": 600, "time_limit": 30, "soft_time_limit": 20}
    task.apply_async(countdown=5, **kept)
    clocked.advance(5)
    [retry] = clocked.held
    assert {option: retry.options.get(option) for option in kept} == kept
    # The retry's own schedule replaces the original's.
    assert (retry.countdown, retry.eta) == (30, timezone.now() + timedelta(seconds=30))
