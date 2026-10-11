"""Celery delivery and scheduled recovery for neutral handoff histories."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

import pytest
from pytest_obligation.crash_histories import CallableDelivery, DeliverySession

from due.django.conformance.background import accounted_background_work, frozen_scheduler_clock
from due.django.public import DurableWorkCapability, UniverseDurableWork
from due.django.runtime.registry import registration_for


@dataclass(frozen=True)
class WorkTableDelivery[W: UniverseDurableWork, ResultT]:
    capability: DurableWorkCapability[W, ResultT]

    @property
    def name(self) -> str:
        return registration_for(self.capability.model).name

    @contextmanager
    def session(self) -> Iterator[DeliverySession]:
        entry = registration_for(self.capability.model)
        with (
            frozen_scheduler_clock() as clock,
            pytest.MonkeyPatch.context() as patcher,
            accounted_background_work(patcher, clock) as driver,
        ):
            driver.hold()

            def recover() -> None:
                driver.holding = False
                # Advance the actual clocks beyond the lease, then run the same
                # scheduled task and published worker bodies as production.
                clock.shift(self.capability.policy.lease_duration + timedelta(seconds=entry.cadence_seconds))
                driver.run_recovery(entry.name)
                # Reaping a dead execution can owe a delayed retry. Honor
                # the configured first retry delay before the next Beat tick.
                clock.shift(self.capability.policy.delay_at(1) + timedelta(seconds=entry.cadence_seconds))
                driver.run_recovery(entry.name)

            with CallableDelivery(
                name=entry.name,
                deliver=driver.release,
                lose=driver.held.clear,
                recover=recover,
            ).session() as session:
                yield session
