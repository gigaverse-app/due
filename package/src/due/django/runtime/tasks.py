"""Celery transport for registered work-table capabilities.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from datetime import datetime, timedelta
from uuid import UUID

from billiard.einfo import ExceptionInfo
from celery import Task, shared_task
from django.db import close_old_connections

from due.django.capabilities.authoring import DurableWorkCapability
from due.django.runtime.limits import (
    EXECUTION_SOFT_TIME_LIMIT_SECONDS,
    EXECUTION_TIME_LIMIT_SECONDS,
)
from due.django.runtime.names import OBSERVE_TASK, RECOVERY_TASK, RUN_TASK
from due.django.runtime.registry import registration_named


class DatabaseTask(Task):
    """Django request-style connection boundaries, including with Celery reuse.

    Return pooled sockets after a delivery and recheck health before the next.
    This never retries the task or decides whether a transaction committed.
    Eager execution borrows its caller's connection and must leave it alone.
    """

    def before_start(self, task_id: str, args: tuple[object, ...], kwargs: dict[str, object]) -> None:
        if not self.request.is_eager:
            close_old_connections()

    def after_return(
        self,
        status: str,
        retval: object,
        task_id: str,
        args: tuple[object, ...],
        kwargs: dict[str, object],
        einfo: ExceptionInfo | None,
    ) -> None:
        if not self.request.is_eager:
            close_old_connections()


def capability_for(name: str) -> DurableWorkCapability:
    registration = registration_named(name)
    capability = registration.capability
    if not isinstance(capability, DurableWorkCapability):
        raise TypeError(f"{name}: registry requires a durable-work capability")
    if capability.policy.lease_duration <= timedelta(seconds=registration.execution_time_limit):
        raise ValueError(f"{name}: lease must outlive the Celery hard execution limit")
    return capability


@shared_task(
    base=DatabaseTask,
    name=RUN_TASK,
    time_limit=EXECUTION_TIME_LIMIT_SECONDS,
    soft_time_limit=EXECUTION_SOFT_TIME_LIMIT_SECONDS,
)
def run_durable_work_task(name: str, work_id: int, delivery_token: str) -> bool:
    return capability_for(name).attempt_execution(work_id, UUID(delivery_token))


@shared_task(base=DatabaseTask, name=RECOVERY_TASK, time_limit=60, soft_time_limit=50)
def recover_durable_work_task(name: str) -> int:
    return capability_for(name).recover()


@shared_task(
    base=DatabaseTask,
    name=OBSERVE_TASK,
    time_limit=EXECUTION_TIME_LIMIT_SECONDS,
    soft_time_limit=EXECUTION_SOFT_TIME_LIMIT_SECONDS,
)
def observe_durable_work_task(name: str, work_id: int, delivery_token: str) -> bool:
    from due.django.capabilities.observed_operations import ObservedExternalOperation

    capability = capability_for(name)
    if not isinstance(capability, ObservedExternalOperation):
        raise TypeError(f"{name}: capability does not declare UNKNOWN observation")
    return capability.attempt_observation(work_id, delivery_token)


@shared_task(base=DatabaseTask, time_limit=60, soft_time_limit=50)
def wake_scheduled_work_task(name: str, work_id: int, revision: int, available_at: str) -> None:
    capability = capability_for(name)
    # Keep the existing task name compatible with already queued scheduled hints.
    if capability.deadline_wakeup_horizon is None:
        raise TypeError(f"{name}: capability does not enable deadline wakeups")
    capability.deliver_deadline_hint(work_id, revision, datetime.fromisoformat(available_at))
