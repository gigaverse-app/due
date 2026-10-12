"""
Production observation delivery through the test job's Redis, prefork Celery and PostgreSQL.

The provider boundary uses the root test provider; this does not qualify deployed
LiveKit playback.
"""

import json
import time
from base64 import b64decode
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker
from django.db import connections
from django.db.models import Q
from redis import Redis

from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState
from due.django.public import ExecutionEligibility, database_now
from due.django.runtime.limits import (
    EXECUTION_SOFT_TIME_LIMIT_SECONDS,
    EXECUTION_TIME_LIMIT_SECONDS,
)
from due.django.runtime.registry import installed
from due.django.runtime.tasks import observe_durable_work_task
from tests.harness.broker import real_broker as real_broker
from tests.harness.observation import (
    ObservationProvider,
    ObservedPublication,
    ObservedWork,
    uncertain_work,
)
from tests.harness.observation import observer as observer
from tests.harness.operation import ProviderResult
from tests.harness.primitive import PrimitiveOwner


def _await_settlement(work: ObservedWork) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        work.refresh_from_db()
        if work.state == DurableWorkState.SUCCEEDED and work.observation_claimed_at is None:
            assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "observed"
            return
        time.sleep(0.05)
    pytest.fail(f"observation worker did not settle work {work.pk}")


@pytest.mark.parametrize("gate_closed", [False, True], ids=["ungated", "execution-parked"])
def test_real_observation_worker_preserves_budget_and_recovers_lost_delivery(
    observer: ObservedPublication, real_broker: Celery, monkeypatch: pytest.MonkeyPatch, gate_closed: bool
) -> None:
    app = real_broker
    task = app.tasks[observe_durable_work_task.name]
    name = "observation_self_test"
    work, lost = uncertain_work(observer), uncertain_work(observer)
    if gate_closed:
        observer = replace(observer, eligibility=ExecutionEligibility(lambda now: Q(pk__in=[])))
    registration = replace(installed.get()[name], capability=observer, queue=app.conf.task_default_queue)
    with (
        patch.dict(installed.get(), {name: registration}),
        Redis.from_url(app.conf.broker_url) as redis,
    ):
        # Celery annotations can change task defaults. The production publisher
        # must carry the lease-compatible budget in each actual broker message.
        monkeypatch.setattr(task, "time_limit", 900)
        monkeypatch.setattr(task, "soft_time_limit", 800)
        ObservationProvider.result = ProviderResult(value="observed")
        observer.recover()
        queue_key = app.conf.broker_transport_options["global_keyprefix"] + registration.queue
        messages = redis.lrange(queue_key, 0, -1)
        assert isinstance(messages, list), "the synchronous Redis client must return broker messages"
        assert len(messages) == 2
        tokens: dict[int, str] = {}
        for raw in messages:
            message = json.loads(raw)
            assert message["headers"]["timelimit"] == [
                EXECUTION_TIME_LIMIT_SECONDS,
                EXECUTION_SOFT_TIME_LIMIT_SECONDS,
            ]
            args = json.loads(b64decode(message["body"]))[0]
            assert args[0] == name
            tokens[args[1]] = args[2]
            if args[1] == lost.pk:
                assert redis.lrem(queue_key, 1, raw) == 1
        connections.close_all()
        with start_worker(app, pool="prefork", concurrency=1, perform_ping_check=False, shutdown_timeout=15):
            _await_settlement(work)
            duplicate = task.apply_async(args=(name, work.pk, tokens[work.pk]), queue=registration.queue)
            assert duplicate.get(timeout=10) is False
            ObservedWork.objects.filter(pk=lost.pk).update(
                observation_claimed_at=database_now() - observer.policy.lease_duration - timedelta(seconds=1)
            )
            observer.recover()
            _await_settlement(lost)
            stale = task.apply_async(args=(name, lost.pk, tokens[lost.pk]), queue=registration.queue)
            assert stale.get(timeout=10) is False
            for settled in (work, lost):
                assert observer.evidence_store.read(attempts_for(settled).get()) == ObservationProvider.result
                settled.refresh_from_db()
                assert settled.attempt_count == 1 and settled.rearm_count == 0
