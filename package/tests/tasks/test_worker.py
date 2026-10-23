"""Real Redis delivery, prefork process death, and PostgreSQL recovery."""

import os
import time
from datetime import timedelta

import pytest
from celery.contrib.testing.worker import start_worker
from django.db import connections
from django.tasks import task
from django.test import override_settings
from django.utils import timezone

from due.django.conformance.registrations import durable_work_installed
from due.django.runtime.tasks import run_durable_work_task
from due.django.tasks.models import TaskWork
from due.django.tasks.runtime import django_tasks_registration
from due.django.testing import hold_nudges
from tests.harness.broker import real_broker as real_broker
from tests.tasks import functions
from tests.tasks.conftest import TASK_OPTIONS

pytestmark = pytest.mark.django_db(transaction=True)


def await_result(result, predicate):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result.refresh()
        if predicate(result):
            return
        time.sleep(0.05)
    pytest.fail(f"Task did not reach expected state: {result!r}")


def test_prefork_worker_recovers_lost_notification_and_process_death(real_broker, tmp_path):
    queue = real_broker.conf.task_default_queue
    config = {
        **TASK_OPTIONS,
        "QUEUES": [queue],
        "OPTIONS": {
            **TASK_OPTIONS["OPTIONS"],
            "replay_safe_tasks": [*TASK_OPTIONS["OPTIONS"]["replay_safe_tasks"], "tests.tasks.functions.die_once"],
        },
    }
    with override_settings(TASKS={"default": config}):
        registration = django_tasks_registration(recovery_queue=queue)
        with durable_work_installed(registration):
            with hold_nudges():
                lost = task(functions.async_add, queue_name=queue).enqueue(4, 5)
                crashed = task(functions.die_once, queue_name=queue, takes_context=True).enqueue(
                    str(tmp_path / "effect")
                )
            assert lost.attempts == crashed.attempts == 0
            connections.close_all()
            with start_worker(
                real_broker, pool="prefork", concurrency=1, perform_ping_check=False, shutdown_timeout=15
            ):
                registration.capability.recover()
                await_result(lost, lambda result: result.is_finished)
                assert lost.return_value == 9
                assert lost.worker_ids[0].split(":")[-1] != str(os.getpid())
                await_result(crashed, lambda result: result.attempts == 1 and (tmp_path / "effect").exists())
                row = TaskWork.objects.get(result_id=crashed.id)
                old_token = row.fencing_token
                TaskWork.objects.filter(pk=row.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
                registration.capability.recover()
                await_result(crashed, lambda result: result.is_finished)
                assert crashed.return_value == {"value": "durable effect", "attempt": 2}
                assert len(crashed.errors) == 1
                duplicate = run_durable_work_task.apply_async(
                    args=(registration.name, row.pk, str(old_token)),
                    queue=queue,
                )
                assert duplicate.get(timeout=10) is False
                crashed.refresh()
                assert crashed.attempts == 2
