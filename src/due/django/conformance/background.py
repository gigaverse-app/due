"""
Background-work driver for behaviour goldens: a fake Celery broker and beat.

Replaces Celery dispatch (``Task.apply_async`` and its ``Celery.send_task``
fallback, including restored instance methods) and the beat
scheduler so a golden can say "the request returned, the workers ran, a minute
passed, the beat ticked" without naming the mechanism that delivers work. That
is what lets one golden judge both a hand-rolled Celery/Redis protocol on
``main`` and its durable-work rewrite: product outcomes are compared, transport
is executed the same way on both sides.

* an immediate dispatch (``.delay`` / ``apply_async`` without an ETA) runs the
  real task body inline, through the task object, as a worker would; a
  dispatch issued from inside a running event loop is held and delivered on
  the next ``drain`` (a worker never shares the caller's loop);
* a dispatch with ``countdown`` / ``eta`` is held until the test clock passes
  its ETA (``advance``), then delivered;
* ``background_beat.run_beat`` runs the selected ``CELERY_BEAT_SCHEDULE``
  entries once, the way beat would, and delivers whatever they dispatched;
* a paused task name models a lost message or a worker that is not running:
  its deliveries are held until ``resume``;
* a task that retries (``self.retry`` or ``autoretry_for``) is republished the
  way a worker publishes ``retry.sig``: same task id, the arguments it retried
  with, ``retries`` one higher, the original publication's queue, expiry and
  time limits, and an ETA from the retry's countdown or ETA, so it is held and
  delivered like any scheduled message. Celery's own ``max_retries`` check then
  turns the run after the last allowed retry into an ordinary failure. A retry
  published while a ``drain`` runs waits for the next ``drain`` call even when
  it is already due, so a task retrying forever with no delay takes one
  attempt per call instead of never returning. Full jitter
  (``retry_backoff``'s default) draws its upper bound, the un-jittered
  backoff, so a retry's ETA does not depend on the random seed;
* a worker exception is an observation, not a pass: every recorded failure
  must be accounted for — raised to the test by ``run_recovery`` or claimed by
  type with ``expect_failures`` — or the ``background_work`` fixture fails the
  test at teardown. A golden that reaches its final product state through a
  second delivery cannot hide a genuine crash in the first. A retry the test
  never lets run counts the same way, under the exception it was retrying: the
  last thing that message did was raise; a retry lost from the broker
  (``drop_held``) counts too, since nothing will run that message again. A
  retry that runs and succeeds needs no claim; the task declared that raise
  retryable.

The durable-work library, when installed, keeps its due times on the database
clock. Its public testing control shares this fixture clock when the library
is installed, so owner deadlines, lease checks and retries advance together.
Nothing here depends on the library being present or inspects its rows.
"""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

import pytest
import time_machine
from asgiref.sync import AsyncSingleThreadContext
from celery import Celery, Task
from celery.exceptions import Retry
from celery.result import EagerResult
from celery.utils import time as celery_time
from django.utils import timezone


# pytest-global: Reached only through contract proofs imported by the collection plugin; never registered as a plugin, so no fixture or setup becomes global.
@dataclass(eq=False)
class Delivery:
    """
    One Celery dispatch as the broker saw it.

    Compared by identity: two failed deliveries of the same task, arguments and
    re-raised exception are still two failures, and accounting one must not
    account the other.
    """

    task: Task
    args: tuple
    kwargs: dict[str, Any]
    task_id: str | None
    eta: datetime | None
    countdown: float | None
    #: ``request.retries`` the task runs with: 0 for a publication, n for its n-th retry.
    retries: int = 0
    #: The publication options as sent (queue, expiry, time limits, ...); a retry keeps them.
    options: dict[str, Any] = field(default_factory=dict)
    delivered: bool = False
    error: BaseException | None = None
    #: The ``Retry`` the task body raised, when it asked to run again instead of finishing.
    retry: Retry | None = None
    #: The message the broker published for that ``retry``; its delivery decides how this one ended.
    retried_as: Delivery | None = None

    @property
    def name(self) -> str:
        return self.task.name

    @property
    def retry_pending(self) -> bool:
        """The task asked to retry and the retry has not run, so its last outcome is still a raise."""
        return self.retried_as is not None and not self.retried_as.delivered

    @property
    def raised(self) -> BaseException | None:
        """The failure, or for a pending retry what it was retrying (the ``Retry`` itself when it names nothing)."""
        if self.error is not None:
            return self.error
        if self.retry_pending:
            assert self.retry is not None
            return self.retry.exc or self.retry
        return None


# pytest-global: Reached only through contract proofs imported by the collection plugin; never registered as a plugin, so no fixture or setup becomes global.
@dataclass
class AcceptedProviderRequest:
    """
    Provider-owned work survives a lost local response and later requests.

    The callback changes only fake provider state. Application transitions must
    still run through production entry points; completing this object does not
    manufacture a receipt or settle a database work row.
    """

    apply: Callable[[], None]
    completed: bool = False

    def lose_response(self) -> None:
        raise TimeoutError("Provider accepted request; local response lost")

    def complete(self) -> None:
        assert not self.completed, "Provider request already completed"
        self.apply()
        self.completed = True


# pytest-global: 2c observes task publications through this shared broker instead of a second, narrower observer; importing it registers nothing.
@dataclass
class BackgroundWork:
    """Replaces the Celery broker/beat seam; see the module docstring."""

    clock: time_machine.Traveller | None = None
    dispatches: list[Delivery] = field(default_factory=list)
    held: list[Delivery] = field(default_factory=list)
    executed: list[Delivery] = field(default_factory=list)
    failures: list[Delivery] = field(default_factory=list)
    #: Failures the test has seen: raised by ``run_recovery`` or claimed by
    #: ``expect_failures``. Everything else is unexpected at teardown.
    accounted: list[Delivery] = field(default_factory=list)
    paused: set[str] = field(default_factory=set)
    holding: bool = False
    provider_requests: list[AcceptedProviderRequest] = field(default_factory=list)

    def accept_provider_request(self, apply: Callable[[], None]) -> AcceptedProviderRequest:
        """Capture fake provider work without applying it or returning completion evidence."""
        request = AcceptedProviderRequest(apply)
        self.provider_requests.append(request)
        return request

    def install(self, monkeypatch: pytest.MonkeyPatch) -> BackgroundWork:
        driver = self

        def apply_async(
            task,
            args=None,
            kwargs=None,
            task_id=None,
            producer=None,
            link=None,
            link_error=None,
            shadow=None,
            **options,
        ):
            driver._dispatch(task, args, kwargs, task_id, options)
            return EagerResult(task_id or "", None, "SENT")

        def send_task(
            app: Celery,
            name: str,
            args: tuple[object, ...] | list[object] | None = None,
            kwargs: dict[str, Any] | None = None,
            task_id: str | None = None,
            **options: Any,
        ) -> EagerResult:
            driver._dispatch(app.tasks[name], args, kwargs, task_id, options)
            return EagerResult(task_id or "", None, "SENT")

        monkeypatch.setattr(Task, "apply_async", apply_async)
        # Full jitter draws a retry countdown at random (0 or 1 s on a first retry), so whether a drain
        # finds the retry due depended on the seed. Every caller, ``autoretry_for`` and production code
        # calling ``get_exponential_backoff_interval(full_jitter=True)`` itself, draws through this module.
        monkeypatch.setattr(celery_time, "random", _JitterAtItsBound())
        # Instance-level MonkeyPatch restoration can shadow the class method
        # with its original bound method. Its eventual broker boundary must
        # still be fake; it must never reach Redis or the real Celery producer.
        monkeypatch.setattr(Celery, "send_task", send_task)
        return self

    # Recording

    def _dispatch(self, task: Task, args, kwargs, task_id, options: dict[str, Any]) -> Delivery:
        if task.name == "due.django.runtime.tasks.run_durable_work_task":
            # pytest-global: Runs only when a test dispatches durable work through this broker; a function-local import adds no collection-time setup.
            # pytest-global: Runs only when a test dispatches durable work through this broker; a function-local import adds no collection-time setup.
            from due.django.conformance.bindings import (
                assert_registered_worker_options,
            )
            from due.django.runtime.registry import installed

            name = args[0] if args else kwargs["name"]
            assert_registered_worker_options(installed.get()[name], options)
        if task.name == "due.django.runtime.tasks.observe_durable_work_task":
            # pytest-global: Validate the actual observation transport only when dispatched.
            # pytest-global: Observation has its own fixed bounds, independent of mutation budgets.
            from due.django.runtime.limits import (
                EXECUTION_SOFT_TIME_LIMIT_SECONDS,
                EXECUTION_TIME_LIMIT_SECONDS,
            )
            from due.django.runtime.registry import installed

            name = args[0] if args else kwargs["name"]
            assert options["queue"] == installed.get()[name].queue
            assert options["time_limit"] == EXECUTION_TIME_LIMIT_SECONDS
            assert options["soft_time_limit"] == EXECUTION_SOFT_TIME_LIMIT_SECONDS
        countdown = options.get("countdown")
        eta = options.get("eta")
        if eta is None and countdown:
            eta = timezone.now() + timedelta(seconds=countdown)
        retries = options.get("retries") or 0
        delivery = Delivery(task, tuple(args or ()), dict(kwargs or {}), task_id, eta, countdown, retries, options)
        self.dispatches.append(delivery)
        if eta is None and not self.holding and task.name not in self.paused and not _loop_running():
            self.deliver(delivery)
        else:
            self.held.append(delivery)
        return delivery

    def _republish(self, delivery: Delivery, retry: Retry) -> Delivery:
        """
        Publish a retry the way a worker does: ``retry.sig``, through this broker, always as a scheduled message.

        Executed eagerly, ``Task.retry`` raises without publishing (celery
        ``app/task.py``: ``if is_eager: raise ret``); a worker calls
        ``S.apply_async()``. The signature carries the task id, the arguments
        the task retried with and ``retries`` one higher. A worker's signature
        also keeps the request's queue, expiry and time limits; an eager
        request has none, so they come from the original publication. The ETA
        comes from ``retry.when``, the countdown or ETA the task asked for, so
        even a zero countdown is held instead of re-entering the task inside
        this ``deliver``.
        """
        # celery-types declares ``sig`` only as an ``__init__`` parameter; ``Retry.__init__`` stores it.
        signature = retry.sig  # pyrefly: ignore [missing-attribute]
        assert signature is not None, f"{delivery.name}: an eager Retry always carries the signature to publish"
        options = {key: value for key, value in delivery.options.items() if key not in ("countdown", "eta")}
        options.update((key, value) for key, value in signature.options.items() if value is not None)
        task_id = options.pop("task_id", delivery.task_id)
        when = retry.when
        if not isinstance(when, datetime):
            when = timezone.now() + timedelta(seconds=float(when or 0))
        options["eta"] = when
        return self._dispatch(delivery.task, signature.args, signature.kwargs, task_id, options)

    def redeliver(self, delivery: Delivery) -> None:
        """Deliver the same captured message again, without inventing a new publisher/options."""
        assert delivery in self.dispatches, "redelivery must originate from a captured production publication"
        self.deliver(replace(delivery, delivered=False, error=None, retry=None, retried_as=None))

    def dispatched(self, task: Task) -> list[Delivery]:
        return [delivery for delivery in self.dispatches if delivery.name == task.name]

    def published_for(self, task: Task) -> list[Any]:
        """The first argument of each publication of ``task``, in order: a dispatch recorder that reads the broker."""
        deliveries = self.dispatched(task)
        assert all(delivery.args for delivery in deliveries), (
            f"{task.name} was published without a positional argument; read the identity from "
            f"`dispatched({task.name})` kwargs instead"
        )
        return [delivery.args[0] for delivery in deliveries]

    def work_deliveries(self, name: str, *, include_observations: bool = False) -> list[Delivery]:
        """Captured work sends for one registration, in broker order."""
        return [
            delivery
            for delivery in self.dispatches
            if (
                delivery.name == "due.django.runtime.tasks.run_durable_work_task"
                or include_observations
                and delivery.name == "due.django.runtime.tasks.observe_durable_work_task"
            )
            and (delivery.args[0] if delivery.args else delivery.kwargs["name"]) == name
        ]

    def run_recovery(self, name: str) -> int:
        """Run production recovery, then consume its due deliveries through this broker."""
        # pytest-global: Runs only when a test asks this broker for production recovery; a function-local import adds no collection-time setup.
        from due.django.runtime.tasks import recover_durable_work_task

        before = len(self.failures)
        holding = self.holding
        self.holding = True
        try:
            count = recover_durable_work_task(name)
        finally:
            self.holding = holding
        self.drain(registration=name)
        # Synchronous recovery tests retain their exception oracle; the normal
        # broker path still records the same error for independent inspection.
        if len(self.failures) > before:
            failure = self.failures[before]
            assert failure.error is not None
            self.accounted.append(failure)
            raise failure.error
        return count

    def unexpected_failures(self) -> list[Delivery]:
        """Failures and still-pending retries the test has not accounted for, in delivery order."""
        return [
            delivery
            for delivery in self.executed
            if (delivery.error is not None or delivery.retry_pending) and delivery not in self.accounted
        ]

    def expect_failures(self, *errors: type[BaseException]) -> list[Delivery]:
        """
        Claim exactly the not-yet-accounted worker failures, by exact exception type and in order.

        A fault-injection history names the errors it injected; anything else
        — a wrong type, an extra failure, or a missing one — fails here rather
        than being absorbed by a later successful delivery. Types match
        exactly, not by subclass: claiming ``RuntimeError`` (or ``Exception``)
        must not absorb a ``TypeError`` or a ``DatabaseError`` subclass nobody
        injected.
        """
        unexpected = self.unexpected_failures()
        observed = [type(failure.raised) for failure in unexpected]
        assert len(observed) == len(errors) and all(
            actual is expected for actual, expected in zip(observed, errors, strict=True)
        ), (
            f"expected worker failures {[error.__name__ for error in errors]}, observed "
            f"{[self._described(failure) for failure in unexpected]}"
        )
        self.accounted.extend(unexpected)
        return unexpected

    def assert_no_unexpected_failures(self) -> None:
        unexpected = self.unexpected_failures()
        assert not unexpected, (
            "workers failed and the test never accounted for it — claim injected faults with "
            "expect_failures(...); anything else is a crash a later delivery papered over, or a retry "
            "still scheduled (advance past its ETA to let it run) or lost: "
            f"{[self._described(failure) for failure in unexpected]}"
        )

    def _described(self, delivery: Delivery) -> tuple[str, str]:
        """A failure as the assertion messages list it; a pending retry says when it would run, or that it was lost."""
        retry = delivery.retried_as
        if delivery.error is not None or retry is None:
            return delivery.name, repr(delivery.raised)
        fate = f"held until {retry.eta}" if retry in self.held else "lost before delivery"
        return delivery.name, f"{delivery.raised!r}, retry {retry.retries} {fate}"

    # Delivery

    def deliver(self, delivery: Delivery) -> None:
        """Run one message's task body now, as a worker would; a failure is recorded, not raised."""
        delivery.delivered = True
        self.executed.append(delivery)
        try:
            delivery.task.apply(
                args=delivery.args,
                kwargs=delivery.kwargs,
                task_id=delivery.task_id,
                retries=delivery.retries,
                throw=True,
            )
        except Retry as retry:
            # This delivery is done; how it ended is decided by the retry it published.
            delivery.retry = retry
            delivery.retried_as = self._republish(delivery, retry)
        except Exception as error:  # noqa: BLE001 - a worker failure is an observation, not a test crash
            delivery.error = error
            self.failures.append(delivery)

    def drain(self, *, registration: str | None = None) -> list[Delivery]:
        """
        Deliver every held dispatch that is due, until nothing more becomes due.

        A retry published by a delivery this drain runs waits for the next
        call, even when it is due now, so a drain always returns: a task that
        retries forever with no delay (``max_retries=None``, a zero countdown)
        takes one attempt per call.
        """
        delivered: list[Delivery] = []
        executed_before = len(self.executed)
        while True:
            now = timezone.now()
            if self.holding:
                return delivered
            # BOUND: retries this drain's deliveries (and what they ran inline) published wait for the next call.
            republished = {id(run.retried_as) for run in self.executed[executed_before:] if run.retried_as is not None}
            due = [
                d
                for d in self.held
                if d.name not in self.paused and (d.eta is None or d.eta <= now) and id(d) not in republished
            ]
            if registration is not None:
                registered = self.work_deliveries(registration, include_observations=True)
                due = [delivery for delivery in due if delivery in registered]
            if not due:
                return delivered
            for delivery in due:
                self.held.remove(delivery)
                self.deliver(delivery)
                delivered.append(delivery)

    def advance(self, seconds: float) -> list[Delivery]:
        """Let time pass on the shared wall/database clock and deliver what became due."""
        assert self.clock is not None, "advance() needs the scheduler clock"
        self.clock.shift(timedelta(seconds=seconds))
        return self.drain()

    # Fault injection at the transport

    def hold(self) -> None:
        """No worker is consuming: every dispatch is held until ``release``."""
        self.holding = True

    def release(self) -> list[Delivery]:
        """Workers come back: deliver everything that is due, in dispatch order."""
        self.holding = False
        return self.drain()

    def pause(self, *tasks: Task) -> None:
        """Hold deliveries of these tasks (lost message / worker down) until ``resume``."""
        self.paused.update(task.name for task in tasks)

    def resume(self, *tasks: Task) -> list[Delivery]:
        for task in tasks:
            self.paused.discard(task.name)
        return self.drain()

    def drop_held(self, task: Task) -> list[Delivery]:
        """
        Lose every held message of a task for good (broker loss).

        A lost retry does not undo the raise it was retrying: nothing will run
        that message again, so the delivery that asked for it stays an
        unaccounted failure, reported as lost, until the test claims it.
        """
        lost = [d for d in self.held if d.name == task.name]
        for delivery in lost:
            self.held.remove(delivery)
        return lost


class _JitterAtItsBound:
    """
    ``random`` as ``celery.utils.time`` sees it under the broker: full jitter draws its upper bound.

    ``get_exponential_backoff_interval`` draws ``randrange(countdown + 1)``, so
    the bound is the un-jittered countdown, the longest wait production can
    pick. Any other draw from that module fails loudly here instead of
    silently becoming deterministic.
    """

    @staticmethod
    def randrange(stop: int) -> int:
        return stop - 1


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@contextmanager
def frozen_scheduler_clock(at: datetime | None = None) -> Iterator[time_machine.Traveller]:
    """
    Freeze product time at ``at`` (default: now) and make the database clock follow it.

    The durable library reads its own clock from SQL; ``use_database_clock``
    points it at the travelled wall clock, so shifting the traveller moves
    both. Every harness that lets time pass — goldens, handoff histories,
    recovery horizons — uses this one scope.

    The default start is the later of the host and PostgreSQL clocks. Rows
    stamped before entry by a database default (``Now()``) carry the real
    database clock, which can run ahead of the host (Docker's VM clock does
    locally); freezing at the host clock would put them in the future, so a
    row stamped "due now" would not be due inside the scope.
    """
    from due.django.testing import database_now, use_database_clock

    start = at if at is not None else max(timezone.now(), database_now())
    clock_scope = use_database_clock(timezone.now)
    with time_machine.travel(start, tick=False) as traveller, clock_scope:
        yield traveller


# pytest-global: Not global: this module is imported, never registered as a plugin, so the fixture stays opt-in for tests that import it.
@pytest.fixture
def scheduler_clock() -> Iterator[time_machine.Traveller]:
    """
    One advancing clock for product time and Due.

    Own the async adapter executor for the same scope; a golden must not leave
    provider-loop workers behind after its assertion fails.
    """
    with AsyncSingleThreadContext(), frozen_scheduler_clock() as traveller:
        yield traveller


@contextmanager
def accounted_background_work(
    monkeypatch: pytest.MonkeyPatch, clock: time_machine.Traveller | None = None
) -> Iterator[BackgroundWork]:
    """An installed driver whose scope fails on any worker failure the body left unaccounted."""
    driver = BackgroundWork(clock=clock).install(monkeypatch)
    yield driver
    driver.assert_no_unexpected_failures()


# pytest-global: Not global: this module is imported, never registered as a plugin, so the fixture stays opt-in for tests that import it.
@pytest.fixture
def background_work(
    monkeypatch: pytest.MonkeyPatch, scheduler_clock: time_machine.Traveller
) -> Iterator[BackgroundWork]:
    """The driver, over the scheduler clock; unaccounted worker failures fail the test."""
    with accounted_background_work(monkeypatch, scheduler_clock) as driver:
        yield driver
