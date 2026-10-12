"""Bind duplicate database deliveries without claiming raw-effect idempotency."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial

from pytest_obligation.completion import DeliveryReplay

from due.django.capabilities import authoring
from due.django.conformance.binding import Binding, entered_binding
from due.django.conformance.examples import DeliveryExample
from due.django.conformance.provider import ProviderProbe
from due.django.public import DatabaseEffect, UniverseDurableWork
from due.django.testing import hold_nudges


@contextmanager
def database_replay[W: UniverseDurableWork, O](
    capability: DatabaseEffect,
    example: Binding[DeliveryExample[W, O]],
) -> Iterator[DeliveryReplay[O]]:
    """Replay one token; count real kernel entries and application calls separately."""
    with entered_binding(example) as delivery, hold_nudges():
        capability.enqueue_execution(delivery.work.pk)
        delivery.work.refresh_from_db()
        token = delivery.work.fencing_token
        assert delivery.effect_calls is not None, "database replay needs actual callback entry counts"
        entries = ProviderProbe(authoring.run_work, key=lambda *args, **kwargs: args[1])
        with entries.patch(authoring, "run_work"):
            yield DeliveryReplay(
                deliver=partial(capability.attempt_execution, delivery.work.pk, token),
                entries=lambda: entries.count(delivery.work.pk),
                observe=delivery.observe,
                expected=delivery.expected,
                callback_entries=delivery.effect_calls,
            )
