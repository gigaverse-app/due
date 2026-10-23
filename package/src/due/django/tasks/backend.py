"""Standard Django Tasks API with transactional PostgreSQL admission."""

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.checks import Error
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, router, transaction
from django.tasks import Task
from django.tasks.backends.base import BaseTaskBackend
from django.tasks.base import TaskError, TaskResult, TaskResultStatus
from django.tasks.exceptions import InvalidTask, TaskResultDoesNotExist
from django.tasks.signals import task_enqueued
from django.utils.module_loading import import_string
from pydantic import JsonValue

from due.django.integration import current_tenant
from due.django.models import DurableWorkState
from due.django.public import DurableWorkPolicy
from due.django.runtime.registry import registration_for
from due.django.runtime.transport import notify_after_commit

from .models import TaskWork


def json_value(value: Any) -> JsonValue:
    """Enforce a real JSON round trip, including PostgreSQL's finite-number rule."""
    return json.loads(json.dumps(value, allow_nan=False))


def task_for(work: TaskWork) -> Task:
    definition = import_string(work.task_path)
    function = definition.func if isinstance(definition, Task) else definition
    return Task(
        func=function,
        backend=work.backend,
        priority=0,
        queue_name=work.queue_name,
        run_after=work.run_after,
        takes_context=work.takes_context,
    )


def result_for(work: TaskWork, *, task: Task | None = None) -> TaskResult:
    status = {
        DurableWorkState.READY: TaskResultStatus.READY,
        DurableWorkState.QUEUED: TaskResultStatus.READY,
        DurableWorkState.RETRYABLE: TaskResultStatus.READY,
        DurableWorkState.CLAIMED: TaskResultStatus.RUNNING,
        DurableWorkState.SUCCEEDED: TaskResultStatus.SUCCESSFUL,
        DurableWorkState.EXHAUSTED: TaskResultStatus.FAILED,
        DurableWorkState.TERMINAL_FAILED: TaskResultStatus.FAILED,
    }[DurableWorkState(work.state)]
    result = TaskResult(
        task=task or task_for(work),
        id=str(work.result_id),
        status=status,
        enqueued_at=work.created_at,
        started_at=work.started_at,
        last_attempted_at=work.last_attempted_at,
        finished_at=work.completed_at,
        args=work.args,
        kwargs=work.kwargs,
        backend=work.backend,
        errors=[TaskError(**error) for error in work.errors],
        worker_ids=list(work.worker_ids),
    )
    object.__setattr__(result, "_return_value", work.return_value)
    return result


class DueBackend(BaseTaskBackend):
    """Replay-safe task invocations using Due's shared execution and recovery."""

    supports_defer = True
    supports_async_task = True
    supports_get_result = True
    supports_priority = False

    def __init__(self, alias: str, params: dict[str, Any]) -> None:
        super().__init__(alias, params)
        if not settings.USE_TZ:
            raise ImproperlyConfigured("Due Tasks requires USE_TZ=True")
        if not isinstance(params.get("QUEUES", ["default"]), (list, tuple)):
            raise ImproperlyConfigured("Due Tasks QUEUES must be a list of queue names")
        allowed = {"replay_safe_tasks", "max_retries", "retry_delay", "retention", "lease_duration"}
        unknown = self.options.keys() - allowed
        if unknown:
            raise ImproperlyConfigured(f"Due Tasks: unknown OPTIONS: {', '.join(sorted(unknown))}")
        paths = self.options.get("replay_safe_tasks", [])
        if not isinstance(paths, (list, tuple)) or not paths or not all(isinstance(p, str) and p for p in paths):
            raise ImproperlyConfigured(
                "Due Tasks requires OPTIONS['replay_safe_tasks']: import paths of replay-safe tasks"
            )
        self.replay_safe_tasks = frozenset(paths)
        if not self.queues or any(not isinstance(q, str) or not q or len(q) > 128 for q in self.queues):
            raise ImproperlyConfigured("Due Tasks requires a nonempty QUEUES list of names up to 128 characters")
        if len(alias) > 128:
            raise ImproperlyConfigured("Due Tasks backend alias must fit in 128 characters")
        values = {"max_retries": 3, "retry_delay": 60, "retention": 604800, "lease_duration": 300}
        values.update({key: value for key, value in self.options.items() if key in values})
        for key, value in values.items():
            if key == "retention" and value is None:
                continue
            if type(value) is not int or value < 0 or (key in {"retention", "lease_duration"} and value == 0):
                raise ImproperlyConfigured(f"Due Tasks {key} must be a nonnegative integer (positive for durations)")
        self.policy = DurableWorkPolicy(
            max_rearms=values["max_retries"],
            retry_delay=timedelta(seconds=values["retry_delay"]),
            lease_duration=timedelta(seconds=values["lease_duration"]),
            retention=None if values["retention"] is None else timedelta(seconds=values["retention"]),
        )

    def validate_task(self, task: Task) -> None:
        super().validate_task(task)
        if task.module_path not in self.replay_safe_tasks:
            raise InvalidTask(f"{task.module_path} is not declared in replay_safe_tasks")
        if len(task.module_path) > 512:
            raise InvalidTask("Task import path must fit in 512 characters")

    def _registration(self):
        from .runtime import TaskCapability

        try:
            entry = registration_for(TaskWork)
        except (KeyError, RuntimeError) as error:
            raise ImproperlyConfigured(
                "Install django_tasks_registration() with configure_durable_work() at startup"
            ) from error
        if not isinstance(entry.capability, TaskCapability) or entry.capability.policy != self.policy:
            raise ImproperlyConfigured("All Due Tasks aliases must use the registered shared execution policy")
        if self.policy.lease_duration <= timedelta(seconds=entry.execution_time_limit):
            raise ImproperlyConfigured("Due Tasks lease_duration must exceed the worker hard time limit")
        return entry

    @staticmethod
    def _validate_database() -> None:
        if connection.vendor != "postgresql":
            raise ImproperlyConfigured("Due Tasks requires PostgreSQL as the default database")
        if router.db_for_write(TaskWork) != "default" or router.db_for_read(TaskWork) != "default":
            raise ImproperlyConfigured("Due Tasks requires the default database for reads and writes")

    def enqueue(self, task: Task, args: tuple[Any, ...] | list[Any], kwargs: dict[str, Any]) -> TaskResult:
        self.validate_task(task)
        definition = import_string(task.module_path)
        if (definition.func if isinstance(definition, Task) else definition) is not task.func:
            raise InvalidTask("Task import path must resolve to its decorated function")
        entry = self._registration()
        # Due's coordination uses default: silently routing admission elsewhere
        # would lose both atomic product admission and recovery ownership.
        self._validate_database()
        normalized_args = json_value(args)
        normalized_kwargs = json_value(kwargs)
        with transaction.atomic():
            fields = {} if task.run_after is None else {"available_at": task.run_after}
            # A new row for EVERY enqueue is the invocation-identity invariant.
            work = TaskWork.objects.create(
                backend=self.alias,
                task_path=task.module_path,
                queue_name=task.queue_name,
                args=normalized_args,
                kwargs=normalized_kwargs,
                takes_context=task.takes_context,
                run_after=task.run_after,
                universe=current_tenant(),
                **fields,
            )
            result = result_for(work, task=task)
            transaction.on_commit(lambda: task_enqueued.send_robust(type(self), task_result=result), robust=True)
            notify_after_commit(entry.name, entry.capability.enqueue_execution, work.pk)
        return result

    def get_result(self, result_id: str) -> TaskResult:
        self._validate_database()
        try:
            identity = UUID(result_id)
        except (ValueError, TypeError, AttributeError) as error:
            raise TaskResultDoesNotExist(result_id) from error
        try:
            work = TaskWork.objects.get(result_id=identity, backend=self.alias)
        except TaskWork.DoesNotExist as error:
            raise TaskResultDoesNotExist(result_id) from error
        return result_for(work)

    def check(self, **kwargs: Any) -> list[Error]:
        try:
            self._registration()
            self._validate_database()
        except ImproperlyConfigured as error:
            return [Error(str(error), id="due_tasks.E001")]
        return []
