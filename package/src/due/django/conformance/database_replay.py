"""Replay a database delivery, including its production transaction and fence."""

from collections.abc import Iterator
from contextlib import contextmanager

from pytest_obligation.profiles.harmless_replay import ReplaySafeEffect

from due.django.conformance.binding import Binding, entered_binding
from due.django.conformance.examples import DeliveryExample
from due.django.public import DatabaseEffect, UniverseDurableWork
from due.django.testing import hold_nudges


@contextmanager
def database_replay[W: UniverseDurableWork, O](
    capability: DatabaseEffect,
    example: Binding[DeliveryExample[W, O]],
) -> Iterator[ReplaySafeEffect]:
    """Assert repeated delivery preserves a declared product outcome.

    The callback itself is deliberately not replay-safe: its transaction is.
    Count delivery attempts at this boundary; separately require exactly one
    callback entry when the example supplies an effect counter.
    """
    with entered_binding(example) as delivery, hold_nudges():
        capability.enqueue_execution(delivery.work.pk)
        delivery.work.refresh_from_db()
        token = delivery.work.fencing_token
        attempts = 0
        before = delivery.effect_calls() if delivery.effect_calls is not None else 0

        def execute(work: W) -> None:
            nonlocal attempts
            attempts += 1
            capability.attempt_execution(work.pk, token)

        def observe(work: W) -> O:
            if attempts and delivery.effect_calls is not None:
                assert delivery.effect_calls() - before == 1, "database delivery repeated or skipped its callback"
            return delivery.observe()

        yield ReplaySafeEffect(
            name=f"{capability.work_name} database delivery",
            prepare=lambda: delivery.work,
            execute=execute,
            expected=lambda work: delivery.expected,
            observe=observe,
            execution_count_for=lambda work: attempts,
        )
