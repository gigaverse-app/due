"""Bind duplicate database deliveries without claiming raw-effect idempotency."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from uuid import uuid4

import psycopg
from celery.app.trace import build_tracer
from django.db import connection
from pytest_obligation.completion import DeliveryReplay

from due.django.capabilities import authoring
from due.django.conformance.binding import Binding, entered_binding
from due.django.conformance.examples import DeliveryExample
from due.django.conformance.provider import ProviderProbe
from due.django.public import DatabaseEffect, DurableWork, UniverseDurableWork
from due.django.runtime.registry import registration_for
from due.django.runtime.tasks import run_durable_work_task
from due.django.testing import hold_nudges


@contextmanager
def database_replay[W: UniverseDurableWork, O](
    capability: DatabaseEffect,
    example: Binding[DeliveryExample[W, O]],
    *,
    connection_loss: bool = False,
) -> Iterator[DeliveryReplay[O]]:
    """Replay one token; count real kernel entries and application calls separately."""
    with entered_binding(example) as delivery, hold_nudges():
        capability.enqueue_execution(delivery.work.pk)
        delivery.work.refresh_from_db()
        token = delivery.work.fencing_token
        assert delivery.effect_calls is not None, "database replay needs actual callback entry counts"

        def identity(model: type[DurableWork], work_id: int, **kwargs: object) -> tuple[type[DurableWork], int]:
            return model, work_id

        def disconnected_delivery() -> None:
            # Fault injection only: kill the connection between worker deliveries.
            # Celery's production tracer owns hooks, body execution and settlement.
            connection.ensure_connection()
            assert connection.connection is not None
            pid = connection.connection.info.backend_pid
            with psycopg.connect(**connection.get_connection_params(), autocommit=True) as observer:
                observer.execute("SELECT pg_terminate_backend(%s)", (pid,))
            # Build the same tracer a worker builds at startup. Rebuild per proof
            # so one proof's cached hooks cannot hide another's negative control.
            trace = build_tracer(run_durable_work_task.name, run_durable_work_task, app=run_durable_work_task.app)
            result = trace(
                str(uuid4()),
                [registration_for(capability.model).name, delivery.work.pk, str(token)],
                {},
                {"is_eager": False},
            )
            connection.close()  # test-host cleanup, after the real delivery has returned
            assert result.info is None, f"worker delivery failed after connection loss: {result.retval}"

        entries = ProviderProbe(authoring.run_work, key=identity)
        with entries.patch(authoring, "run_work"):
            yield DeliveryReplay(
                deliver=disconnected_delivery
                if connection_loss
                else partial(capability.attempt_execution, delivery.work.pk, token),
                entries=lambda: entries.count((capability.model, delivery.work.pk)),
                observe=delivery.observe,
                expected=delivery.expected,
                callback_entries=delivery.effect_calls,
            )
