# Use Due with Django Tasks

Keep Django's `@task`, `.enqueue()`, and `TaskResult` APIs while Due stores each
invocation in PostgreSQL and uses Celery workers to execute it. A committed
invocation survives a lost broker notification or a worker dying mid-task.

This optional integration ships in **`django-due`**. Enable `due.django.tasks`
only where you want its task table. Native Due capabilities continue to work
alongside it. Requirements are the same as Due: Python 3.14, Django 6.0.8 or
later in the 6.x series, PostgreSQL, Celery, and a supported Celery broker.
The older `django_tasks` backport is not supported.

This integration is available starting with **Due 0.8.0**.

Follow [Acme Printworks](../../docs/acme-printworks-tutorial.md) for a runnable
journey from a word-count task to guarded publication, portal repair and print
ordering. **Always build the application contract as you adopt Due.** Test your
Django task's transaction and replay assumptions, then enroll every native
capability with the [pytest-obligation conformance suite](conformance.md).
The shared runtime's tests cannot establish your application's provider semantics.

## Install and configure

```bash
pip install 'django-due[redis]>=0.8.0'
```

Add both apps and your task configuration to `settings.py`:

```python
INSTALLED_APPS = [
    # Your existing Django apps...
    "due.django",
    "due.django.tasks",
    "myapp.apps.MyAppConfig",
]

TASKS = {
    "default": {
        "BACKEND": "due.django.tasks.backend.DueBackend",
        "QUEUES": ["default", "emails"],
        "OPTIONS": {
            "replay_safe_tasks": ["myapp.tasks.build_preview"],
        },
    },
}

CELERY_BROKER_URL = "redis://localhost:6379/0"
USE_TZ = True
```

Use your existing PostgreSQL `DATABASES["default"]`. Admission, product writes,
and Due coordination must use that same database. A database router must route
the task model's reads and writes to `default`.

The explicit `replay_safe_tasks` list declares functions you have checked can
run again after a crash. Use stable provider idempotency keys, deterministic
overwrites, or equivalent application guarantees. Adding a path to this list
does **not** make an unsafe side effect replay-safe.

Register the task capability in your application startup hook:

```python
# myapp/apps.py
from django.apps import AppConfig


class MyAppConfig(AppConfig):
    name = "myapp"

    def ready(self):
        from due.django.runtime.registry import configure_durable_work
        from due.django.tasks.runtime import django_tasks_registration

        configure_durable_work(
            [
                django_tasks_registration(recovery_queue="default"),
                # Include your native Due registrations here too.
            ]
        )
```

Call `configure_durable_work()` once with the complete manifest. The helper
adds Due's Celery task imports and periodic recovery schedule to Django's Celery
settings. Your Celery app should load these settings in the usual way:

```python
# project/celery.py
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")
app = Celery("project")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
```

Then migrate and start a worker and Beat scheduler:

```bash
python manage.py migrate
python manage.py check
celery -A project.celery:app worker -Q default,emails --loglevel=INFO
celery -A project.celery:app beat --loglevel=INFO
```

Keep Beat running. It owns recovery and dispatch of delayed tasks, even when
normal immediate notifications appear to work. Every queue in `QUEUES` and the
registration's `recovery_queue` needs a worker consuming it. No Celery result
backend is required; Django task results live in PostgreSQL.

## Define and enqueue

```python
# myapp/tasks.py
from pathlib import Path

from django.tasks import task


@task
def build_preview(document_id: int, text: str) -> str:
    # Example only: provision this directory on storage shared by your workers.
    path = Path("/srv/previews") / f"{document_id}.txt"
    path.write_text(text)
    return str(path)
```

Use an immutable document revision for the identity if different revisions can
run concurrently. The task adapter fences its **result row**; it cannot fence an
arbitrary external file write or prevent two distinct invocations writing to the
same destination. For current-state synchronization, use a native Due
[projection](capabilities.md) with its explicit execution/completion split.

```python
from datetime import timedelta

from django.tasks import TaskResultStatus
from django.utils import timezone
from myapp.tasks import build_preview

result = build_preview.enqueue(42, "Hello")
later = build_preview.using(
    queue_name="emails",
    run_after=timezone.now() + timedelta(minutes=10),
).enqueue(43, "Later")

# In a later request, using the saved ID:
result = build_preview.get_result(result.id)
result.refresh()
if result.status == TaskResultStatus.SUCCESSFUL:
    print(result.return_value)
```

A successful result retains the errors from earlier attempts.
Arguments and return values must survive strict JSON serialization. Tuples
become lists, dictionary keys become strings, and NaN/infinity are rejected.
Do not pass model instances, credentials, or large binary payloads.

Coroutine task bodies are supported. Async callers use `await task.aenqueue()`
and `await result.arefresh()`. `@task(takes_context=True)` receives Django's
`TaskContext`, including the persisted result ID and a one-based actual execution
attempt count. Broker reservations do not count as executions.

## Commit the task with the product change

```python
from django.db import transaction

with transaction.atomic():
    document = Document.objects.create(text="Hello")
    result = build_preview.enqueue(document.pk, document.text)
    # Both rows commit together; no worker notification occurs before commit.
```

Each enqueue creates a distinct UUID result, including identical calls. A
rollback, including a savepoint rollback, removes its provisional result and
discards the notification. After rollback, lookup raises
`TaskResultDoesNotExist`. Avoid wrapping enqueue in `transaction.on_commit()`:
doing so moves durable admission outside the product transaction.

For async views, put the product write and enqueue inside one synchronous
transactional function and call it with `sync_to_async`. An async enqueue does
not join a transaction in another thread or database.

## Features and policy

| Feature | Behavior |
| --- | --- |
| Enqueue/result APIs | Sync and async, including refresh and task-mismatch checks |
| Task bodies | Sync and coroutine functions with importable module paths |
| Context | Result identity, actual starts, timestamps and worker process IDs |
| Delays | `run_after` stored in PostgreSQL; dispatched by recovery when eligible |
| Queues | Persisted per invocation and honored by immediate and recovery delivery |
| Multiple backend aliases | Separate result lookup; all Due aliases share the registered execution policy |
| Priority | Unsupported; nonzero priority raises `InvalidTask` before admission |
| Retries | Bounded by Due; errors and tracebacks retained across attempts |
| Worker death | Lease recovery permits another attempt of replay-safe work |
| Signals | `task_enqueued`, `task_started`, `task_finished`; committed snapshots, robust receivers |
| Result retention | Successful invocations pruned by recovery after seven days by default |
| Failed invocations | Retained for diagnosis; no automatic deletion |
| Backport | `django_tasks` is unsupported; use Django's built-in `django.tasks` |

`task_finished` describes the final successful or exhausted outcome. A retryable
failure remains `READY`; it does not emit a terminal signal. Signals are
best-effort notifications and may be lost if a process dies after commit. Use
persisted results for correctness decisions.

Backend `OPTIONS`, in addition to the required `replay_safe_tasks` list:

| Option | Default | Meaning |
| --- | ---: | --- |
| `max_retries` | 3 | Additional attempts after failed execution or an expired executing claim |
| `retry_delay` | 60 | Seconds before a failed invocation becomes eligible again |
| `lease_duration` | 300 | Seconds of claim ownership; must exceed the hard worker limit |
| `retention` | 604800 | Seconds to keep successful results; `None` retains them indefinitely |

These options apply to the shared task capability. All Due backend aliases must
use the same values. `replay_safe_tasks` and `QUEUES` can differ by alias.
Unknown options and invalid values fail explicitly.

`django_tasks_registration()` accepts `alias="default"`,
`recovery_queue="default"`, `cadence_seconds=30`,
`execution_time_limit=60`, and `execution_soft_time_limit=50`.
Set the time limits for your workload, keeping `soft < hard < lease`.
Lost **unaccepted delivery reservations** do not consume the task retry budget.
The recovery interval and retry delay affect when work runs; `run_after` is an
earliest start time, not an execution deadline.

## Operate and migrate

Inspect `due.django.tasks.models.TaskWork` by `result_id` to relate a Django
result to its Due work ID, state, fencing token, and retry history. The `django_tasks`
registration participates in Due's existing recovery metrics and operational
commands. See [operations](operations.md).

Only the worker owning the current claim can commit a return value or a failure.
A task body can still execute twice, and its own database writes or external
effects are not part of the fenced result transaction. Due does not promise
exactly-once task execution. Use native evidence-aware capabilities for
non-repeatable operations whose outcomes can remain uncertain.

For cutover, route new invocations to Due while the previous backend drains its
own queue and serves old result IDs. Keep backend aliases, queue consumers, task
import paths, and replay declarations available until their work and result
retention windows drain. Do not reinterpret another backend's stored rows as Due
invocations. See the [migration guide](migrations/django-tasks.md).
