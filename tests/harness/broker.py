"""Isolated Redis namespace for real observation-worker controls."""

import os
from collections.abc import Iterator
from uuid import uuid7

import pytest
from celery import Celery
from celery.contrib.testing.app import setup_default_app
from redis import Redis


@pytest.fixture
def real_broker() -> Iterator[Celery]:
    broker_url = os.environ.get("DUE_BROKER_URL", "redis://127.0.0.1:56389/0")
    # global_keyprefix also covers kombu's bindings and unacked keys, so a crashed run leaves only keys
    # under its own prefix, which no later run reads.
    prefix = f"durable-smoke-{uuid7().hex}-"
    with (
        Redis.from_url(broker_url) as redis,
        Celery("durable_smoke", broker=broker_url, backend=broker_url, set_as_current=False, fixups=[]) as app,
        setup_default_app(app),
    ):
        app.conf.update(
            task_default_queue=prefix,
            broker_transport_options={"global_keyprefix": prefix},
            result_backend_transport_options={"global_keyprefix": prefix},
            task_serializer="json",
            accept_content=["json"],
            result_serializer="json",
        )
        # setup_default_app restores state; explicit binding makes shared_task
        # proxies publish to this isolated app instead of the repository default.
        app.set_current()
        app.set_default()
        try:
            yield app
        finally:
            # Callers stop their worker before teardown; never flush a shared Redis DB.
            for key in redis.scan_iter(match=f"{prefix}*"):
                redis.delete(key)
