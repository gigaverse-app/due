"""
Route delivery through a real Redis broker and a prefork Celery worker.

The broker is the test job's Redis on its own index, and PostgreSQL the normal isolated
test database. Redis keys and the queue have a unique prefix; no deployed application
worker is involved.
"""

import time
from datetime import timedelta

import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker
from django.db import connections, transaction
from django.db.models import F, Value
from django.db.models.functions import Concat
from django.utils import timezone

from due.django.native.recovery import RecoveryLease, RecoveryRoute
from due.django.testing import hold_nudges
from tests.harness.broker import real_broker as real_broker
from tests.harness.primitive import PrimitiveOwner, owed_owners
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = pytest.mark.usefixtures("isolated_primitive")


def _complete(identity: int) -> None:
    # Each body execution leaves a durable mark; a duplicate would add another !.
    PrimitiveOwner.objects.filter(pk=identity).update(value=Concat(F("value"), Value("!")), completed_at=timezone.now())


def _await_completion(owner: PrimitiveOwner) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        owner.refresh_from_db()
        if owner.completed_at is not None and owner.claimed_at is None:
            assert owner.value == "before!"
            return
        time.sleep(0.05)
    pytest.fail(f"worker did not complete owner {owner.pk}")


def test_real_worker_consumes_progress_once_and_recovers_a_lost_notification(real_broker: Celery) -> None:
    app = real_broker

    @app.task(name="tests.route_smoke.deliver", time_limit=5, soft_time_limit=4, lazy=False, shared=False)
    def deliver(identity: int, delivery_token: str | None = None) -> bool:
        return route.attempt_delivery(identity, delivery_token, run=_complete)

    route = RecoveryRoute(
        lease=RecoveryLease(model=PrimitiveOwner, claimed_at="claimed_at", duration=timedelta(seconds=30)),
        owed=owed_owners,
        task=deliver,
        reservation_slack=timedelta(seconds=20),
        max_delay=timedelta(seconds=2),
    )
    owner = PrimitiveOwner.objects.create()
    with transaction.atomic():
        route.request_progress(owner.pk, delay=timedelta(seconds=1))
        owner.refresh_from_db()
        assert owner.claimed_at is None
    owner.refresh_from_db()
    assert owner.claimed_at is not None
    token = owner.claimed_at.isoformat()

    # Fork with no open PostgreSQL sockets; each worker owns its connection.
    connections.close_all()
    with start_worker(app, pool="prefork", concurrency=1, perform_ping_check=False, shutdown_timeout=15):
        _await_completion(owner)
        completed_at = owner.completed_at
        assert deliver.apply_async(args=(owner.pk, token)).get(timeout=10) is False
        owner.refresh_from_db()
        assert owner.value == "before!" and owner.completed_at == completed_at

        lost = PrimitiveOwner.objects.create()
        with hold_nudges() as held:
            route.request_progress(lost.pk)
        assert len(held) == 1
        assert route.recover(owed_owners(), batch_size=10) == 1
        _await_completion(lost)
        assert route.recover(owed_owners(), batch_size=10) == 0
