"""Celery delivery and bounded cross-capability recovery for handoff histories."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
from typing import Any

import time_machine
from celery import Celery
from django.utils import timezone
from pytest_obligation.crash_histories import HandoffHistory
from pytest_obligation.profiles.automatic_recovery import DueWorkSweep, declared_recovery_delay

from due.django.conformance.background import BackgroundWork, frozen_scheduler_clock, harness_owned_background_work
from due.django.conformance.binding import entered_binding
from due.django.public import DurableWorkCapability, UniverseDurableWork
from due.django.runtime.registry import registration_for


@dataclass
class _RecoverySession:
    name: str
    driver: BackgroundWork
    clock: time_machine.Traveller
    delay: timedelta
    rounds: int
    primary_tick: Callable[[], object]
    can_lose: bool = True

    def deliver(self) -> None:
        self.driver.release()

    def lose(self) -> None:
        self.driver.held.clear()

    def recover(self) -> None:
        self.recover_with(())

    def recover_with(self, ticks: tuple[Callable[[], object], ...]) -> None:
        self.driver.holding = False
        for _ in range(self.rounds):
            etas = [delivery.eta for delivery in self.driver.held if delivery.eta is not None]
            self.clock.shift(max([self.delay, *(eta - timezone.now() for eta in etas)]))
            self.driver.drain()
            self.primary_tick()
            for tick in ticks:
                tick()
            self.driver.drain()
        assert not self.driver.held, (
            f"{self.name}: work is still scheduled after {self.rounds} recovery rounds "
            f"({[(delivery.name, delivery.eta) for delivery in self.driver.held]}), "
            "so the history's outcome was never observed"
        )


@dataclass(frozen=True)
class WorkTableDelivery[W: UniverseDurableWork, ResultT]:
    """Drive the production tick plus declared downstream ticks over a bounded horizon.

    Each round advances beyond the lease, first retry delay, cadence and pending
    message ETAs. Instance dispatch recorders are lifted only within the history,
    so publications execute through the same accounted broker as real workers.
    """

    capability: DurableWorkCapability[W, ResultT]
    rounds: int = 3
    round_delay: timedelta = timedelta(hours=1)
    app: Celery | None = None

    def __post_init__(self) -> None:
        if self.rounds < 1 or self.round_delay <= timedelta():
            raise ValueError("handoff recovery needs positive rounds and round_delay")

    @property
    def name(self) -> str:
        return registration_for(self.capability.model).name

    @contextmanager
    def session(self) -> Iterator[_RecoverySession]:
        entry = registration_for(self.capability.model)
        with frozen_scheduler_clock() as clock, harness_owned_background_work(clock, app=self.app) as driver:
            driver.hold()
            yield _RecoverySession(
                name=entry.name,
                driver=driver,
                clock=clock,
                delay=max(self.capability.policy.lease_duration, self.capability.policy.delay_at(1))
                + timedelta(seconds=entry.cadence_seconds)
                + self.round_delay,
                rounds=self.rounds,
                primary_tick=partial(driver.run_recovery, entry.name),
            )


@dataclass
class SweepDelivery:
    """Run an existing lifecycle's sweep through the accounted Celery broker.

    ``binding`` supplies its real sweep and provider scope; ``scope`` optionally
    installs application routing. ``scoped(history)`` shares that exact binding
    with arrangement and restores nested bindings even when a proof fails.
    Recovery uses the same bounded rounds and downstream ticks as work tables.
    """

    binding: Callable[[], AbstractContextManager[DueWorkSweep]]
    name: str = "application sweep"
    scope: Callable[[], AbstractContextManager[object]] = nullcontext
    rounds: int = 3
    round_delay: timedelta = timedelta(hours=1)
    app: Celery | None = None
    _current: ContextVar[DueWorkSweep] = field(default_factory=lambda: ContextVar("delivery_sweep"), init=False)

    def __post_init__(self) -> None:
        if self.rounds < 1 or self.round_delay <= timedelta():
            raise ValueError("handoff recovery needs positive rounds and round_delay")

    def scoped(self, history: HandoffHistory[Any, Any]) -> HandoffHistory[Any, Any]:
        @contextmanager
        def arrange() -> Iterator[Any]:
            with entered_binding(self.binding) as sweep:
                token = self._current.set(sweep)
                try:
                    with entered_binding(history.arrange) as value:
                        yield value
                finally:
                    self._current.reset(token)

        return history.model_copy(update={"arrange": arrange})

    @contextmanager
    def session(self) -> Iterator[_RecoverySession]:
        active = self._current.get(None)
        with nullcontext(active) if active is not None else entered_binding(self.binding) as sweep:
            with (
                entered_binding(self.scope),
                frozen_scheduler_clock() as clock,
                harness_owned_background_work(clock, app=self.app) as driver,
            ):
                driver.hold()
                yield _RecoverySession(
                    name=self.name,
                    driver=driver,
                    clock=clock,
                    delay=declared_recovery_delay(sweep) + self.round_delay,
                    rounds=self.rounds,
                    primary_tick=sweep.run_tick,
                )
