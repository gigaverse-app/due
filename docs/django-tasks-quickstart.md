# Set up Due in a Django project

The [first tutorial](quickstart.md) supplies a prepared project so you can run a
task and try recovery immediately. This guide shows each configuration file for
adding Django's `@task` interface with Due as the backend to your own project.

**The app is deliberately trivial so you can focus on Due.** It sleeps for a
second, prints a greeting, and returns a string. There are no application models,
provider accounts or business workflows to understand.

You will enqueue a function, read its saved result, then deliberately lose a
notification and watch Due recover the work. The application API is **Django's
`@task` and `.enqueue()` with Due as the backend**. Due's setup comes through
`due.django.public`; test helpers come through `due.django.conformance`.

## 1. Create the tiny app

Use Python 3.14, [uv](https://docs.astral.sh/uv/getting-started/installation/), and
Docker Compose on Linux, macOS, or Windows with WSL. Run the shell commands in the
same directory. These instructions install the source version because the new
consolidated imports are not in the 0.10.0 PyPI release. Keep the generated
`uv.lock`, which records the resolved commit.

```bash
mkdir hello-due
cd hello-due
uv init --bare --python 3.14
uv add 'django-due[redis] @ git+https://github.com/gigaverse-app/due.git@main#subdirectory=package'
mkdir hello
touch hello/__init__.py
```

Create `hello/tasks.py`. This is the entire application behavior:

```python
# hello/tasks.py
from time import sleep

from django.tasks import task


@task
def greet(name: str) -> str:
    sleep(1)  # Stand in for some slow work.
    message = f"Hello, {name}!"
    print(message, flush=True)
    return message
```

If a worker dies, Due may run the function again from the beginning, including
printing again. That is acceptable for this example. A real operation such as
placing an order needs a stronger replay contract.

## 2. Start PostgreSQL and Redis

PostgreSQL stores invocations and results. Redis carries Celery's notifications.
Create `compose.yaml` in `hello-due`:

```yaml
# compose.yaml
services:
  postgres:
    image: postgres:17
    environment:
      POSTGRES_USER: tutorial
      POSTGRES_PASSWORD: tutorial
      POSTGRES_DB: tutorial
    ports:
      - "127.0.0.1:55462:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U tutorial"]
      interval: 2s
      timeout: 5s
      retries: 20
  redis:
    image: redis:7
    ports:
      - "127.0.0.1:56412:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 5s
      retries: 20
```

```bash
docker compose up -d --wait
```

These credentials and loopback ports are for this local tutorial. If you already
have dedicated development services, set the `PG*` and `CELERY_BROKER_URL`
environment variables used below instead. Tests need permission to create a test
database.

## 3. Configure Due once

Create `settings.py` in `hello-due`:

```python
# settings.py
import os

SECRET_KEY = "local-due-tutorial-only"
USE_TZ = True
INSTALLED_APPS = ["due.django", "due.django.tasks", "hello.apps.HelloConfig"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("PGDATABASE", "tutorial"),
        "USER": os.getenv("PGUSER", "tutorial"),
        "PASSWORD": os.getenv("PGPASSWORD", "tutorial"),
        "HOST": os.getenv("PGHOST", "127.0.0.1"),
        "PORT": os.getenv("PGPORT", "55462"),
    }
}
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:56412/0")
TASKS = {
    "default": {
        "BACKEND": "due.django.tasks.backend.DueBackend",
        "OPTIONS": {"replay_safe_tasks": ["hello.tasks.greet"]},
    }
}
```

`replay_safe_tasks` declares which functions you have checked can safely run again.
It does not change their behavior or make an unsafe effect safe.

Create `hello/apps.py`. Django calls `ready()` in each process:

```python
# hello/apps.py
from django.apps import AppConfig


class HelloConfig(AppConfig):
    name = "hello"

    def ready(self):
        from celery_app import app
        from due.django.public import configure_durable_work

        configure_durable_work(django_tasks=True, celery_app=app)
```

`configure_durable_work()` installs the task imports and recurring recovery
schedule. `django_tasks=True` uses the default backend and queue, with recovery
every 30 seconds. Call it once; native registrations can share this call later.
Use `django_tasks_registration(...)` from the same module when you need custom
queues, recovery cadence or execution limits.

Create `celery_app.py` in `hello-due`:

```python
# celery_app.py
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
app = Celery("hello_due")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
```

The file layout is:

```text
hello-due/
├── pyproject.toml
├── uv.lock
├── compose.yaml
├── settings.py
├── celery_app.py
└── hello/
    ├── __init__.py
    ├── apps.py
    └── tasks.py
```

Create Due's tables and check the wiring:

```bash
uv run python -m django migrate --settings=settings
uv run python -m django due_check --settings=settings
```

`due_check` should report no configuration errors. It checks registration and
routes; it does not establish that Redis or a worker is reachable.

## 4. Enqueue, execute, read the result

Open a second terminal in `hello-due` and start a worker:

```bash
uv run celery -A celery_app:app worker -Q default --concurrency=1 --loglevel=INFO
```

In a third terminal, also in `hello-due`, start the Beat scheduler:

```bash
uv run celery -A celery_app:app beat --loglevel=INFO
```

The worker runs functions. Beat periodically asks Due to recover outstanding
work, including invocations whose notifications disappeared. Keep both running.

In the first terminal, open a Django shell:

```bash
uv run python -m django shell --settings=settings
```

Paste this Python. Importing the Celery app configures the producer's broker:

```python
from celery_app import app
from django.tasks import TaskResultStatus
from hello.tasks import greet

result = greet.enqueue("Ada")
print(result.id)
```

`.enqueue()` returns a result handle after persisting the invocation. The worker
runs `greet()` and prints `Hello, Ada!` in **its** terminal. In the shell, read the
saved result:

```python
result = greet.get_result(result.id)
print(result.status)
if result.status == TaskResultStatus.SUCCESSFUL:
    print(result.return_value)
```

Expected: `SUCCESSFUL` and `Hello, Ada!`. If the status is still `READY` or
`RUNNING`, repeat the lookup after the worker finishes. Save the result ID to
retrieve it from another request. Results live in PostgreSQL; no Celery result
backend is needed. Arguments and return values must be JSON serializable.

## 5. Lose a notification

Stop **Beat only** with Ctrl+C. Leave the worker and shell running. Use Due's
public fault control to drop the immediate notification:

```python
from due.django.conformance import hold_nudges

with hold_nudges() as dropped:
    recovered = greet.enqueue("Grace")

print(len(dropped))
recovered.refresh()
print(recovered.status)
```

Expected: `1` and `READY`. The task was committed, but the worker was not notified.
Restart Beat. After a recovery tick and the one-second task, the worker should
print `Hello, Grace!`. Inspect its saved result:

```python
recovered.refresh()
print(recovered.status)
if recovered.status == TaskResultStatus.SUCCESSFUL:
    print(recovered.return_value)
```

Expected: `SUCCESSFUL` and `Hello, Grace!`. You did not enqueue it again. Recovery
found the committed invocation in PostgreSQL and notified the worker. The
30-second cadence is a polling interval, not a completion deadline; allow about
a minute for this demonstration.
`hold_nudges()` is for demonstrations and tests only.

## 6. Keep a small application test

Exit the shell and stop the worker and Beat. These tests use a separate PostgreSQL
test database and Due's controlled delivery harness. Step 5 exercises the real
worker/scheduler path; the harness makes the failure history repeatable without
running separate processes.

```bash
uv add --dev 'django-due[testing]' pytest-django
```

Create `pytest.ini`:

```ini
# pytest.ini
[pytest]
DJANGO_SETTINGS_MODULE = settings
pythonpath = .
```

Create `test_greeting.py`:

```python
# test_greeting.py
import pytest
from django.db import transaction
from django.tasks import TaskResultStatus
from django.tasks.exceptions import TaskResultDoesNotExist
from due.django.conformance import WorkTableDelivery, hold_nudges
from due.django.runtime.registry import registration_for
from due.django.tasks.models import TaskWork

from hello.tasks import greet


def test_greeting_can_repeat(capsys):
    assert greet.func("Ada") == "Hello, Ada!"
    assert greet.func("Ada") == "Hello, Ada!"
    assert capsys.readouterr().out == "Hello, Ada!\nHello, Ada!\n"


@pytest.mark.django_db(transaction=True)
def test_rollback_removes_the_invocation():
    with pytest.raises(ValueError), hold_nudges(), transaction.atomic():
        result = greet.enqueue("Ada")
        raise ValueError("reject the request")
    with pytest.raises(TaskResultDoesNotExist):
        greet.get_result(result.id)


@pytest.mark.django_db(transaction=True)
def test_lost_notification_recovers_the_greeting(capsys):
    with hold_nudges() as dropped:
        result = greet.enqueue("Grace")
    assert len(dropped) == 1
    result.refresh()
    assert result.status == TaskResultStatus.READY
    assert capsys.readouterr().out == ""
    with WorkTableDelivery(registration_for(TaskWork).capability).session() as delivery:
        delivery.recover()
    result.refresh()
    assert result.status == TaskResultStatus.SUCCESSFUL
    assert result.return_value == "Hello, Grace!"
    assert "Hello, Grace!" in capsys.readouterr().out
```

```bash
uv run pytest -q
```

Expect **3 passing tests**: repeated execution, transactional rollback, and
lost-notification recovery. The recovery test checks that the function really
printed and returned the greeting. These establish this trivial app's promises;
they say nothing about a real provider's replay safety.

## Bring this into your application

Keep `.enqueue()` inside the transaction that saves the product change it serves,
using the same default database. Due commits both together and notifies after
commit. Wrapping enqueue in `transaction.on_commit()` would separate durable
admission from the product transaction. [Transactional example](django-tasks.md#commit-the-task-with-the-product-change).

For a task that can safely repeat, this is the full authoring API. For richer
requirements, build on the existing applications:

- [Which API should I use?](api-guide.md) explains `ReplaySafeEffect.for_owner()`,
  `capability.atomic()`, consolidated imports and the file layout.
- [Acme Printworks](../../docs/acme-printworks-tutorial.md) grows from a Django task
  to guarded publication, remote-state repair, uncertain print orders and cleanup.
  Build its application contract alongside your first native capability; its
  [runnable tests](../../examples/acme_printworks/tests) show enrollment and evidence.
- [Django Tasks reference](django-tasks.md) covers delays, queues, async calls,
  retry budgets, retention and worker time limits.

If a task stays `READY`, check that the worker consumes `default`, all terminals
use the same database/broker settings, and Beat is running. Inspect the worker
log and `uv run python -m django due_status django_tasks --settings=settings`.

When finished, stop the worker and Beat with Ctrl+C, then run `docker compose down`
from `hello-due`. These services are disposable and have no persistent volumes;
keep any data you need before removing them.
