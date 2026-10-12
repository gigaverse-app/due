"""Timed terminal outcomes through Due's registered scheduler and disposable hints."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta

from django.utils import timezone
from pytest_obligation.completion import BoundedCompletion

from due.django.conformance.background import frozen_scheduler_clock, harness_owned_background_work
from due.django.conformance.binding import Binding, entered_binding
from due.django.conformance.examples import DeliveryExample
from due.django.public import DurableWorkCapability, UniverseDurableWork
from due.django.runtime.registry import registration_for
from due.django.runtime.tasks import wake_scheduled_work_task


@contextmanager
def completion[W: UniverseDurableWork, O, ResultT](
    capability: DurableWorkCapability[W, ResultT],
    example: Binding[DeliveryExample[W, O]],
    *,
    within_seconds: float,
    not_before_seconds: float = 0,
    lose_hints: bool = False,
    remaining: Callable[[W], int] | None = None,
) -> Iterator[BoundedCompletion[O]]:
    """Use real cadence, task bodies and DB deadlines; never age a work row."""
    entry = registration_for(capability.model)
    with frozen_scheduler_clock() as clock, harness_owned_background_work(clock) as driver:
        if lose_hints:
            driver.pause(wake_scheduled_work_task)
        with entered_binding(example) as delivery:
            next_tick = timezone.now()

            def recover() -> None:
                nonlocal next_tick
                if timezone.now() >= next_tick:
                    driver.run_recovery(entry.name)
                    next_tick = timezone.now() + timedelta(seconds=entry.cadence_seconds)
                if lose_hints:
                    driver.drop_held(wake_scheduled_work_task)

            yield BoundedCompletion(
                observe=delivery.observe,
                expected=delivery.expected,
                advance=driver.advance,
                recover=recover,
                within_seconds=within_seconds,
                not_before_seconds=not_before_seconds,
                interval_seconds=min(0.1, entry.cadence_seconds),
                remaining=(lambda: remaining(delivery.work)) if remaining is not None else None,
            )
