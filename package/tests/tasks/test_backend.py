from datetime import timedelta
from uuid import UUID

import pytest
from asgiref.sync import async_to_sync
from django.db import transaction
from django.tasks import task, task_backends
from django.tasks.base import TaskResultStatus
from django.tasks.exceptions import InvalidTask, TaskResultDoesNotExist, TaskResultMismatch
from django.tasks.signals import task_enqueued, task_finished, task_started
from django.utils import timezone

from due.django.public import WorkExecutionContext, complete_work
from due.django.tasks.models import TaskWork
from tests.tasks import functions

pytestmark = pytest.mark.django_db(transaction=True)


def execute(delivery):
    from due.django.runtime.tasks import run_durable_work_task

    return run_durable_work_task(*delivery["args"])


@pytest.mark.parametrize("body", [functions.add, functions.async_add])
def test_standard_task_roundtrip_and_distinct_invocations(task_runtime, body):
    _, deliveries = task_runtime
    definition = task(body)
    first, second = definition.enqueue(2, 3), definition.enqueue(2, 3)
    assert first.id != second.id
    assert first.status == TaskResultStatus.READY
    assert first.attempts == 0
    assert execute(deliveries[0])
    assert not execute(deliveries[0])
    first.refresh()
    assert first.return_value == 5
    assert first.attempts == 1
    assert first.enqueued_at is not None and first.started_at is not None and first.finished_at is not None
    assert first.enqueued_at <= first.started_at <= first.finished_at
    assert first.last_attempted_at == first.started_at
    assert definition.get_result(second.id).status == TaskResultStatus.READY


def test_async_enqueue_refresh_and_context(task_runtime):
    _, deliveries = task_runtime
    definition = task(functions.context_value, takes_context=True)
    result = async_to_sync(definition.aenqueue)("hello")
    execute(deliveries[0])
    async_to_sync(result.arefresh)()
    assert result.return_value == {"value": "hello", "attempt": 1, "id": result.id}
    assert async_to_sync(definition.aget_result)(result.id).return_value == result.return_value


def test_admission_joins_outer_transaction_and_savepoint(task_runtime):
    _, deliveries = task_runtime
    definition = task(functions.add)
    with transaction.atomic():
        committed = definition.enqueue(1, 2)
        assert TaskWork.objects.filter(result_id=committed.id).exists()
        assert not deliveries
        with pytest.raises(ValueError), transaction.atomic():
            rolled_back = definition.enqueue(3, 4)
            raise ValueError("rollback savepoint")
    assert len(deliveries) == 1
    with pytest.raises(TaskResultDoesNotExist):
        definition.get_result(rolled_back.id)
    with pytest.raises(ValueError), transaction.atomic():
        outer = definition.enqueue(5, 6)
        raise ValueError("rollback outer")
    with pytest.raises(TaskResultDoesNotExist):
        definition.get_result(outer.id)
    assert len(deliveries) == 1


def test_lost_notification_and_delayed_queue_survive_recovery(task_runtime):
    capability, deliveries = task_runtime
    from due.django.testing import hold_nudges

    with hold_nudges():
        result = task(functions.add).using(queue_name="emails").enqueue(2, 4)
    assert not deliveries
    capability.recover()
    assert deliveries[0]["queue"] == "emails"
    execute(deliveries[0])
    assert task_backends["default"].get_result(result.id).return_value == 6
    delayed = task(functions.add).using(run_after=timezone.now() + timedelta(days=1)).enqueue(3, 4)
    count = len(deliveries)
    capability.recover()
    assert len(deliveries) == count
    assert delayed.status == TaskResultStatus.READY
    TaskWork.objects.filter(result_id=delayed.id).update(available_at=timezone.now() - timedelta(seconds=1))
    capability.recover()
    execute(deliveries[-1])
    delayed.refresh()
    assert delayed.return_value == 7


@pytest.mark.parametrize(
    "body,args,error_type",
    [
        (functions.fail, ["expected failure"], ValueError),
        (functions.invalid_return, [], TypeError),
    ],
)
def test_errors_retry_then_exhaust_with_history(task_runtime, body, args, error_type):
    capability, deliveries = task_runtime
    result = task(body).enqueue(*args)
    for attempt in (1, 2):
        with pytest.raises(error_type):
            execute(deliveries[-1])
        result.refresh()
        assert result.attempts == attempt
        assert len(result.errors) == attempt
        assert result.errors[-1].exception_class is error_type
        assert result.errors[-1].traceback
        if attempt == 1:
            assert result.status == TaskResultStatus.READY
            capability.recover()
    assert result.status == TaskResultStatus.FAILED
    assert result.finished_at is not None
    with pytest.raises(ValueError, match="Task failed"):
        _ = result.return_value


def test_stale_worker_cannot_publish_result_or_metadata(task_runtime):
    capability, deliveries = task_runtime
    result = task(functions.add).enqueue(2, 5)
    row = TaskWork.objects.get(result_id=result.id)
    from due.django.runtime.coordination import accept_delivery

    claim = accept_delivery(
        TaskWork, row.pk, UUID(deliveries[0]["args"][2]), lease_duration=capability.policy.lease_duration
    )
    assert claim is not None
    value = capability.apply(
        claim.work, WorkExecutionContext(claim=claim, lease_duration=capability.policy.lease_duration)
    )
    TaskWork.objects.filter(pk=row.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    capability.recover()
    execute(deliveries[-1])
    assert not complete_work(claim, finalize=lambda work: capability.finalize_success(work, "stale"))
    result.refresh()
    assert result.return_value == value == 7
    assert result.attempts == 2
    assert len(result.errors) == 1


@pytest.mark.parametrize(
    "options", [{"priority": 1}, {"queue_name": "missing"}, {"run_after": timezone.datetime(2027, 1, 1)}]
)
def test_unsupported_options_fail_before_admission(task_runtime, options):
    with pytest.raises(InvalidTask):
        task(functions.add).using(**options)
    assert not TaskWork.objects.exists()


@pytest.mark.parametrize("value", [object(), float("nan"), float("inf")])
def test_invalid_arguments_fail_before_admission(task_runtime, value):
    with pytest.raises((TypeError, ValueError)):
        task(functions.echo).enqueue(value)
    assert not TaskWork.objects.exists()


def test_result_identity_is_scoped_to_backend_and_task(task_runtime):
    result = task(functions.add).enqueue(1, 2)
    for missing in (result.id, "invalid-id"):
        with pytest.raises(TaskResultDoesNotExist):
            task_backends["other"].get_result(missing)
    with pytest.raises(TaskResultMismatch):
        task(functions.echo).get_result(result.id)


def test_importable_decorated_task_roundtrips(task_runtime):
    from tests.tasks.decorated import greet

    _, deliveries = task_runtime
    result = greet.enqueue("Django")
    execute(deliveries[-1])
    assert greet.get_result(result.id).return_value == "Hello Django"


def test_signals_observe_committed_outcomes(task_runtime):
    _, deliveries = task_runtime
    events = []

    def receive(sender, task_result, signal, **kwargs):
        assert not transaction.get_connection().in_atomic_block
        events.append((signal, task_result.status))

    for signal in (task_enqueued, task_started, task_finished):
        signal.connect(receive, weak=False)
    try:
        with transaction.atomic():
            task(functions.add).enqueue(1, 2)
            assert not events
        execute(deliveries[0])
        assert events == [
            (task_enqueued, TaskResultStatus.READY),
            (task_started, TaskResultStatus.RUNNING),
            (task_finished, TaskResultStatus.SUCCESSFUL),
        ]
    finally:
        for signal in (task_enqueued, task_started, task_finished):
            signal.disconnect(receive)


def test_broker_failure_keeps_committed_admission_recoverable(task_runtime):
    from unittest.mock import patch

    from due.django.runtime.tasks import run_durable_work_task

    capability, deliveries = task_runtime
    with patch.object(run_durable_work_task, "apply_async", side_effect=ConnectionError("broker unavailable")):
        result = task(functions.add).enqueue(4, 7)
    row = TaskWork.objects.get(result_id=result.id)
    assert result.attempts == 0
    TaskWork.objects.filter(pk=row.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    capability.recover()
    execute(deliveries[-1])
    result.refresh()
    row.refresh_from_db()
    assert result.return_value == 11
    assert result.attempts == 1
    assert row.rearm_count == 0


def test_retention_prunes_success_but_keeps_failed_work(task_runtime):
    capability, deliveries = task_runtime
    successful = task(functions.add).enqueue(1, 2)
    execute(deliveries[-1])
    failed = task(functions.fail).enqueue("keep failure")
    for _ in range(2):
        with pytest.raises(ValueError):
            execute(deliveries[-1])
        capability.recover()
    TaskWork.objects.filter(result_id__in=[successful.id, failed.id]).update(
        completed_at=timezone.now() - timedelta(days=8),
    )
    capability.recover()
    with pytest.raises(TaskResultDoesNotExist):
        task_backends["default"].get_result(successful.id)
    assert task_backends["default"].get_result(failed.id).status == TaskResultStatus.FAILED


def test_expired_claim_cannot_record_another_start(task_runtime):
    from due.django.public import WorkOwnershipLost
    from due.django.runtime.coordination import accept_delivery

    capability, deliveries = task_runtime
    result = task(functions.add).enqueue(1, 2)
    row = TaskWork.objects.get(result_id=result.id)
    claim = accept_delivery(
        TaskWork, row.pk, UUID(deliveries[0]["args"][2]), lease_duration=capability.policy.lease_duration
    )
    assert claim is not None
    TaskWork.objects.filter(pk=row.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    capability.recover()
    with pytest.raises(WorkOwnershipLost):
        capability.apply(claim.work, WorkExecutionContext(claim=claim, lease_duration=capability.policy.lease_duration))
    result.refresh()
    assert result.attempts == 0


@pytest.mark.parametrize(
    "options", [{"typo": 1}, {"max_retries": -1}, {"retry_delay": True}, {"retention": 0}, {"replay_safe_tasks": "*"}]
)
def test_bad_configuration_fails_explicitly(options):
    from django.core.exceptions import ImproperlyConfigured

    from due.django.tasks.backend import DueBackend

    with pytest.raises(ImproperlyConfigured):
        DueBackend("default", {"OPTIONS": {"replay_safe_tasks": ["tests.tasks.functions.add"], **options}})


def test_unregistered_backend_fails_check_and_enqueue(task_runtime):
    from django.core.exceptions import ImproperlyConfigured

    from due.django.runtime.registry import installed

    with installed.installed({}):
        assert task_backends["default"].check()[0].id == "due_tasks.E001"
        with pytest.raises(ImproperlyConfigured):
            task(functions.add).enqueue(1, 2)
    assert not TaskWork.objects.exists()


def test_database_router_cannot_split_admission_from_recovery(task_runtime):
    from unittest.mock import patch

    from django.core.exceptions import ImproperlyConfigured
    from django.db import router

    with patch.object(router, "db_for_write", return_value="another_database"):
        assert task_backends["default"].check()[0].id == "due_tasks.E001"
        with pytest.raises(ImproperlyConfigured, match="default database"):
            task(functions.add).enqueue(1, 2)
    assert not TaskWork.objects.exists()


@pytest.mark.parametrize("short_setup", [False, True])
def test_tasks_and_native_capabilities_share_manifest(task_runtime, short_setup):
    from django.test import override_settings

    from due.django.runtime.registry import DurableWorkRegistration, configure_durable_work, installed
    from due.django.tasks.runtime import django_tasks_registration
    from tests.harness.primitive import PrimitiveOwner, PrimitiveWork, primitive, primitive_tables

    _, deliveries = task_runtime
    with primitive_tables(), installed.installed({}), override_settings(CELERY_BEAT_SCHEDULE={}, CELERY_IMPORTS=()):
        configure_durable_work(
            [
                DurableWorkRegistration(name="native", capability=primitive),
                *([] if short_setup else [django_tasks_registration()]),
            ],
            django_tasks=short_setup,
        )
        owner = PrimitiveOwner.objects.create()
        with primitive.atomic(owner.pk) as pending:
            pending.request_work(PrimitiveWork(payload="native result"))
        standard = task(functions.add).enqueue(3, 5)
        for delivery in deliveries:
            execute(delivery)
        owner.refresh_from_db()
        standard.refresh()
        assert owner.value == "native result"
        assert standard.return_value == 8
