# Tutorial: run a task, stop its worker, get the result

You have a slow function to run in the background. You want to submit it now,
let a worker do it, and retrieve its result later. If that worker dies, the job
should still get done.

We will use this function:

```python
import os
from time import sleep

from django.conf import settings
from django.tasks import task


@task(queue_name=settings.CELERY_TASK_DEFAULT_QUEUE)
def greet(name: str, seconds: int = 1) -> str:
    print(f"Starting greeting for {name} in process {os.getpid()}", flush=True)
    sleep(seconds)
    message = f"Hello, {name}!"
    print(message, flush=True)
    return message
```

**This app is deliberately trivial so you can focus on Due.** The sleep stands
in for slow work. The prints let you see when it runs. Its return value lets you
check what finished. You do not need an application model or an external account.

The function is already in [tasks.py](../../examples/hello_due/tasks.py). Django's
`@task`, `.enqueue()` and `.get_result()` are the application API; Due is the
backend that persists jobs and recovers unfinished work.

## 1. Use the prepared project

Use Python 3.14, [uv](https://docs.astral.sh/uv/getting-started/installation/) and
Docker Compose. Run the shell commands with Bash, using WSL on Windows:

```bash
git clone https://github.com/gigaverse-app/due.git
cd due
uv sync --all-packages --locked
docker compose up -d --wait
export DJANGO_SETTINGS_MODULE=examples.hello_due.settings
uv run python -m django migrate
uv run python -m django due_check
```

Use the source checkout containing this tutorial; its imports follow release
0.10.0. This prepared project has only Due and the greeting app installed. The
[settings](../../examples/hello_due/settings.py) reuse the repository's local
PostgreSQL and Redis services. If you supply your own dedicated development
services, set `DUE_DATABASE_URL` and `DUE_BROKER_URL` in every terminal instead.

We will use three terminals in this directory: a Python shell, a worker, and a
recovery scheduler. Leave the worker and scheduler stopped for now.

## 2. Submit a job before starting a worker

In terminal 1:

```bash
uv run python -m django shell --settings=examples.hello_due.settings
```

In the Python shell:

```python
from examples.hello_due.tasks import greet

result = greet.enqueue("Ada")
print(result.id)
print(result.status.name)  # READY
```

`.enqueue()` returns without waiting for the function. Nothing has printed a
greeting yet: we have submitted a job, but no worker is running. Due has saved
the function's import path and arguments in PostgreSQL.

Keep the ID if you close this shell. The job survives the submitting process.

## 3. Start a worker and read the result

In terminal 2:

```bash
uv run celery -A examples.hello_due.celery:app worker --pool=solo --concurrency=1 -Q hello-due --loglevel=INFO
```

Among the worker logs, you should see:

```text
Starting greeting for Ada in process ...
Hello, Ada!
```

Back in terminal 1, after the greeting appears:

```python
result.refresh()
print(result.status.name)  # SUCCESSFUL
print(result.return_value)  # Hello, Ada!
```

The result is stored in PostgreSQL. You can retrieve it in a new shell using the
ID you saved:

```python
from examples.hello_due.tasks import greet

result = greet.get_result("paste-the-id-here")
print(result.return_value)
```

Try submitting a few jobs:

```python
results = [greet.enqueue(name) for name in ["Linus", "Margaret", "Edsger"]]
```

The worker runs them one at a time. Each `.enqueue()` creates a separate job,
even when its arguments match another job. After the greetings appear, call
`.refresh()` on each handle to read its status and result.

## 4. Kill the worker while it is busy

Wait for those jobs to finish. Then submit a slower greeting in terminal 1:

```python
interrupted = greet.enqueue("Grace", seconds=10)
print(interrupted.id)
```

As soon as terminal 2 prints `Starting greeting for Grace in process ...`, copy
that process ID. In terminal 3, run the following **before its ten-second sleep
finishes**, entering the ID from that line:

```bash
read -r -p "Worker PID from its Starting line: " worker_pid
kill -KILL "$worker_pid"
```

The worker exits without printing `Hello, Grace!`. If it finished before you
killed it, submit a fresh slow greeting and try again. We use Celery's `solo`
pool here so the printed PID identifies this demo's whole worker. Ctrl+C usually
requests a graceful shutdown, which lets the current job finish.

In terminal 1:

```python
interrupted.refresh()
print(interrupted.status.name)  # RUNNING: its claim has not expired yet
```

PostgreSQL still records unfinished work. It cannot immediately know whether a
worker is dead or just slow, so Due gives each attempt a lease. This demo uses a
20-second lease to keep the experiment short.

Restart the same worker command in terminal 2. In terminal 3, start the recovery
scheduler:

```bash
uv run celery -A examples.hello_due.celery:app beat --loglevel=INFO --schedule=/tmp/hello-due-beat
```

After the old lease expires, Beat's recurring recovery finds the unfinished job
and makes it runnable again. Allow about a minute. The worker prints:

```text
Starting greeting for Grace in process ...
Hello, Grace!
```

Read the **same** result in terminal 1. Do not enqueue another job:

```python
interrupted.refresh()
print(interrupted.status.name)  # SUCCESSFUL
print(interrupted.return_value)  # Hello, Grace!
```

You submitted the job once. Its first attempt died. Due recovered it and saved
the result of a later attempt. Leave Beat running during normal operation so
recovery continues.

**The function starts again from the beginning.** Its first print repeats.
Repeating this greeting is harmless. Check that repeating your own task is safe
before enabling recovery for it. For example, a task that charges a card needs
an operation-specific idempotency or evidence strategy; declaring it replay-safe
does not supply one.

## 5. Submit a task as part of a transaction

A common next step is saving a product change and scheduling its background work
together. Put `.enqueue()` inside the same database transaction. You can see the
rollback behavior without adding an application model:

```python
from django.db import transaction
from django.tasks.exceptions import TaskResultDoesNotExist

try:
    with transaction.atomic():
        rolled_back = greet.enqueue("This should never print")
        raise ValueError("Reject the request")
except ValueError:
    pass

try:
    greet.get_result(rolled_back.id)
except TaskResultDoesNotExist:
    print("No committed job")
```

No greeting runs. Due writes the job in the transaction and notifies the worker
after commit. If your application also writes a product row in that transaction,
both changes commit or roll back together. Keep `.enqueue()` inside that
transaction so the product change and durable job stay together.

## What to keep for your own app

You have used the everyday API: `@task`, `.enqueue()`, `.refresh()` and
`.get_result()`. To use it in an existing Django project:

1. Add `due.django` and `due.django.tasks` to `INSTALLED_APPS`, run their
   migrations, and select `due.django.tasks.backend.DueBackend` in `TASKS`.
2. Put your function in an importable module. List its path in
   `OPTIONS["replay_safe_tasks"]` after checking what happens if it runs again.
3. Register Due in `AppConfig.ready()` and run Celery workers plus Beat.

For the standard queue and timings, registration is one call:

```python
from due.django.public import configure_durable_work

configure_durable_work(django_tasks=True, celery_app=app)
```

Here `app` is your configured Celery application. The prepared demo uses an
[explicit registration](../../examples/hello_due/apps.py) for its own queue and
short timings: recovery every two seconds and a 20-second lease. Standard task
registration uses the `default` queue, a 30-second recovery cadence and a
five-minute lease. A cadence is a polling interval, not a completion deadline.

The [Django Tasks setup guide](django-tasks-quickstart.md) shows every configuration
file when you are ready to wire your own project. The
[example's checks](../../examples/hello_due/README.md#verification) run this journey
with real worker death, restart and Beat recovery, plus a deliberately dropped
notification. They check the actual task output and saved return values.

When the application needs to publish only the current document revision, repair
remote state, or track an uncertain provider outcome, use the
[API guide](api-guide.md) to choose a native capability. The
[native work lesson](native-work-tutorial.md) then explains the admission and
completion hooks; [Printworks](../../docs/acme-printworks-tutorial.md) applies them
to documents and print orders.

When finished, stop the worker and Beat with Ctrl+C, exit the shell, and stop the
local services with `docker compose down`.
