# 1. Run a task, stop its worker, get the result

[All chapters](tutorial.md)

You have a slow function to run in the background. You want to submit it now,
let a worker do it, and retrieve its result later. If that worker dies, the job
should still get done.

If the only record of a job is the message handed to a worker, a worker killed
mid-job (by a deploy, an out-of-memory kill or a crashed machine) takes the job
with it: nobody else knows it was unfinished. In this chapter you will kill a worker
on purpose and watch the job finish anyway.

This chapter uses only Django's standard task API. You will not write any Due
code yet: Due runs underneath, as the backend that stores and recovers jobs.

## 1. Define the task

Here is the complete definition of `greet`, in
[`examples/hello_due/tasks.py`](../../examples/hello_due/tasks.py):

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

`@task` makes this function available for background execution. Its arguments
are the inputs you will pass to `.enqueue()`, and its return value becomes the
job's saved result. `queue_name` tells Due which worker queue should receive it.

The prepared project below already contains this file. Open it as you follow
along; this is the function you can change to experiment with your own work.
Django's `@task`, `.enqueue()` and `.get_result()` are the application API;
Due is the backend that persists jobs and recovers unfinished work.

## 2. Use the prepared project

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

## 3. Submit a job before starting a worker

In terminal 1:

```bash
uv run python -m django shell --settings=examples.hello_due.settings
```

In the Python shell:

```python
from examples.hello_due.tasks import greet

# Due saves a row in PostgreSQL and returns at once; no worker needs to be running.
result = greet.enqueue("Ada")
print(result.id)
print(result.status.name)  # READY
```

`.enqueue()` returns without waiting for the function. Nothing has printed a
greeting yet: we have submitted a job, but no worker is running. Due has saved
the function's import path and arguments as a row in PostgreSQL.

That row is the important idea in this tutorial. The job is not just a message
waiting in Redis; it is a record of **owed work**: something the database knows
still has to be done. Messages can be lost. A committed row is not.

Keep the ID if you close this shell. The job survives the submitting process.

## 4. Start a worker and read the result

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

## 5. Kill the worker while it is busy

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
worker is dead or just slow, so when a worker starts a job it **claims** it,
taking a **lease**: a deadline by which it promises to finish. Until the lease
expires, nobody else touches the job. After it expires, Due assumes the worker is gone. This demo uses
a 20-second lease to keep the experiment short; the standard lease is five
minutes.

Restart the same worker command in terminal 2. In terminal 3, start the recovery
scheduler:

```bash
uv run celery -A examples.hello_due.celery:app beat --loglevel=INFO --schedule=/tmp/hello-due-beat
```

Celery Beat is Celery's scheduler for recurring jobs. Due uses it to run
**recovery**: a periodic scan for owed work that is not making progress, either
because its worker died or because the message announcing it was lost. After the
old lease expires, recovery finds the unfinished job and makes it runnable again.
Allow about a minute. The worker prints:

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
Running a job again after an interrupted attempt is called a **replay**, and a
function that can be replayed without harm is **replay-safe**. Repeating this
greeting is harmless. Check that repeating your own task is safe before enabling
recovery for it. A task that sends an email or charges a card is usually not
replay-safe: the replay sends a second email or makes a second charge. Such work
needs an [idempotency key](vocabulary.md#idempotency-key) the provider honours,
or the [uncertain-outcome tools](external-operation-tutorial.md) from chapter 5.
Calling a task replay-safe in the configuration does not make it so.

## 6. Submit a task as part of a transaction

A common next step is saving a product change and scheduling its background work
together. Put `.enqueue()` inside the same database transaction. You can see the
rollback behavior without adding an application model:

```python
from django.db import transaction
from django.tasks.exceptions import TaskResultDoesNotExist

try:
    with transaction.atomic():
        # The job row is written in this transaction, and the worker is told only after commit.
        rolled_back = greet.enqueue("This should never print")
        raise ValueError("Reject the request")
except ValueError:
    pass

try:
    greet.get_result(rolled_back.id)
except TaskResultDoesNotExist:
    print("No committed job")
```

No greeting runs. Due writes the job's row inside your transaction, and only
sends the worker a message after the transaction commits. If your application
also writes a product row in that transaction, both changes commit or roll back
together. Keep `.enqueue()` inside that transaction so the product change and
the owed work stay together. Due calls this **atomic admission**: work is
admitted (recorded as owed) in the same transaction as the change that needs it.

The post-commit message is called a **nudge**. It only makes the worker start
sooner. If it is lost, recovery still finds the committed row.

## What you learned

| Word | Meaning in this chapter |
| --- | --- |
| [Owed work](vocabulary.md#owed-work-and-the-work-row) | The job's row in PostgreSQL; it outlives workers and lost messages |
| [Lease](vocabulary.md#attempt-claim-and-lease) | How long a worker may hold a job before Due assumes it died |
| [Recovery](vocabulary.md#recovery) | Beat's recurring scan that restarts stalled or forgotten jobs |
| [Replay-safe](vocabulary.md#replay-and-replay-safe) | Safe to run again from the beginning; required for automatic recovery |
| [Atomic admission](vocabulary.md#admission) | The job commits or rolls back with the rest of your transaction |
| [Nudge](vocabulary.md#nudge) | The post-commit message that starts work quickly; losing it is harmless |

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

# Installs Due's task backend, its Celery routes and the Beat recovery schedule.
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

A Django task knows its arguments, but not which row in your app it is working
for. That is enough for a greeting. It is not enough when the user can change
that row while the job runs, and an old result must not replace a newer one.
[Chapter 2](native-work-tutorial.md) adds that link between work and the product
row it belongs to. The [chapter list](tutorial.md) shows the rest.

When finished, stop the worker and Beat with Ctrl+C, exit the shell, and stop the
local services with `docker compose down`.

[Next: own the result](native-work-tutorial.md)
