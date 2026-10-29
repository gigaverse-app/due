"""Bridge task metadata to the existing Due claim and settlement protocol."""

import os
import socket
from traceback import format_exception
from uuid import UUID

from django.db import transaction
from django.tasks.base import TaskContext
from django.tasks.signals import task_finished, task_started
from pydantic import JsonValue

from due.django.capabilities.authoring import DurableWorkCapability
from due.django.identities import WorkId
from due.django.models import DurableWorkState
from due.django.runtime.coordination import (
    WorkExecutionContext,
    WorkOwnershipLost,
    database_now,
    require_fenced_settlement,
)
from due.django.runtime.registry import django_tasks_registration as django_tasks_registration
from due.django.runtime.registry import registration_for
from due.django.runtime.tasks import run_durable_work_task

from .backend import DueBackend, json_value, result_for, task_for
from .models import TaskWork


def publish(work_id: WorkId, token: UUID) -> None:
    entry = registration_for(TaskWork)
    work = TaskWork.objects.only("queue_name").get(pk=work_id)
    # Normal delivery and recovery use the same persisted route.
    run_durable_work_task.apply_async(
        args=(entry.name, work_id, str(token)),
        queue=work.queue_name,
        time_limit=entry.execution_time_limit,
        soft_time_limit=entry.execution_soft_time_limit,
    )


class TaskCapability(DurableWorkCapability[TaskWork, JsonValue]):
    def apply(self, work: TaskWork, context: WorkExecutionContext[TaskWork]) -> JsonValue:
        with context.external_call():
            with transaction.atomic():
                current = context.claim.locked_work()
                now = database_now()
                if current is None or current.lease_expires_at is None or current.lease_expires_at <= now:
                    raise WorkOwnershipLost("Task claim expired before execution")
                # Count actual starts, never delivery reservations or failed publishes.
                current.started_at = current.started_at or now
                current.last_attempted_at = now
                current.worker_ids = [*current.worker_ids, f"{socket.gethostname()}:{os.getpid()}"]
                current.save(update_fields=["started_at", "last_attempted_at", "worker_ids"])
            definition = task_for(current)
            snapshot = result_for(current, task=definition)
            task_started.send_robust(DueBackend, task_result=snapshot)
            args = [TaskContext(task_result=snapshot), *current.args] if current.takes_context else current.args
            return json_value(definition.call(*args, **current.kwargs))

    def finalize_success(self, work: TaskWork, result: JsonValue) -> None:
        # The result and SUCCEEDED transition commit under the SAME captured fence.
        require_fenced_settlement(work)
        work.return_value = result
        work.save(update_fields=["return_value"])
        self._finished(work)

    def finalize_retry(self, work: TaskWork, error: Exception) -> None:
        self._error(work, error)

    def finalize_terminal(self, work: TaskWork, error: Exception) -> None:
        self._error(work, error)

    def _error(self, work: TaskWork, error: Exception) -> None:
        require_fenced_settlement(work)
        exception_type = type(error)
        work.errors = [
            *work.errors,
            {
                "exception_class_path": f"{exception_type.__module__}.{exception_type.__qualname__}",
                "traceback": "".join(format_exception(error)),
            },
        ]
        work.save(update_fields=["errors"])
        if work.state in {DurableWorkState.EXHAUSTED, DurableWorkState.TERMINAL_FAILED}:
            self._finished(work)

    @staticmethod
    def _finished(work: TaskWork) -> None:
        # Observers see only committed outcomes; receivers cannot undo settlement.
        transaction.on_commit(
            lambda: task_finished.send_robust(DueBackend, task_result=result_for(work)),
            robust=True,
        )
