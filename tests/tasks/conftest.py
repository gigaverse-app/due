from unittest.mock import patch

import pytest
from django.test import override_settings

from due.django.conformance.registrations import durable_work_installed
from due.django.runtime.tasks import run_durable_work_task
from due.django.tasks.runtime import django_tasks_registration

TASK_OPTIONS = {
    "BACKEND": "due.django.tasks.backend.DueBackend",
    "QUEUES": ["default", "emails"],
    "OPTIONS": {
        "replay_safe_tasks": ["tests.tasks.decorated.greet"]
        + [
            f"tests.tasks.functions.{name}"
            for name in (
                "add",
                "async_add",
                "context_value",
                "fail",
                "invalid_return",
                "echo",
            )
        ],
        "retry_delay": 0,
        "max_retries": 1,
    },
}


@pytest.fixture
def task_runtime():
    deliveries = []
    with override_settings(TASKS={"default": TASK_OPTIONS, "other": TASK_OPTIONS}):
        entry = django_tasks_registration()
        with (
            durable_work_installed(entry),
            patch.object(run_durable_work_task, "apply_async", side_effect=lambda **kw: deliveries.append(kw)),
        ):
            yield entry.capability, deliveries
