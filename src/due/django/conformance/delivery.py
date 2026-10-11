"""Celery delivery and bounded cross-capability recovery for handoff histories."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

import pytest
import time_machine
from celery import Celery, current_app
from django.utils import timezone

from due.django.conformance.background import BackgroundWork, accounted_background_work, frozen_scheduler_clock
from due.django.public import DurableWorkCapability, UniverseDurableWork
from due.django.runtime.registry import registration_for


@dataclass
class _WorkTableSession:
    name: str
    driver: BackgroundWork
    clock: time_machine.Traveller
    delay: timedelta
    rounds: int
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
            self.driver.run_recovery(self.name)
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
    def session(self) -> Iterator[_WorkTableSession]:
        entry = registration_for(self.capability.model)
        app = self.app if self.app is not None else current_app
        with frozen_scheduler_clock() as clock, pytest.MonkeyPatch.context() as patcher:
            for task in app.tasks.values():
                for method in ("delay", "apply_async"):
                    if method in vars(task):
                        patcher.delattr(task, method)
            with accounted_background_work(patcher, clock) as driver:
                driver.hold()
                yield _WorkTableSession(
                    name=entry.name,
                    driver=driver,
                    clock=clock,
                    delay=max(self.capability.policy.lease_duration, self.capability.policy.delay_at(1))
                    + timedelta(seconds=entry.cadence_seconds)
                    + self.round_delay,
                    rounds=self.rounds,
                )
