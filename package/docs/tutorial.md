# Learn Due, one capability at a time

## Does any of this sound familiar?

Most Django apps start background work the same way: save something, then put a
job on a queue. That works until production traffic, deploys and slow providers
find the gaps:

- **A job never ran, and nothing logged an error.** The worker was restarted
  mid-job, or the queue message was lost after the database commit.
- **A job ran for a request that failed.** The job was queued before the
  transaction rolled back.
- **A job ran twice.** One worker looked dead, so another started the same job.
- **An old result replaced a new one.** The user changed their input, and the
  slow job for the old input finished last.
- **A customer was charged twice.** The provider accepted the first request, but
  the reply timed out, so the app tried again.

None of these are bugs in your function. They happen *between* your function
and the database, the queue and the provider. Due is a Django library that
closes those gaps. It stores the fact that work is **owed** in PostgreSQL, next
to your data, and it recovers that work after crashes and lost messages, without
letting an outdated attempt overwrite a newer result.

If those bold words are new, the [vocabulary page](vocabulary.md) defines each
one in a sentence or two, starting from the failure it addresses. You can start
chapter 1 without it.

## What you will build

Each chapter adds one requirement to a deliberately small application:
arithmetic, short text files and local SQLite tables. There is no provider
account, network API or real business logic to set up, so everything you observe
is Due's behaviour, not the application's.

| Chapter | The problem it solves | What you will do | Due API it introduces |
| --- | --- | --- | --- |
| [1. Run and recover a task](quickstart.md) | Jobs lost when a worker dies | Enqueue a function, kill its worker, get the result anyway | None of its own: Django's `@task` with Due as the backend |
| [2. Own the result](native-work-tutorial.md) | An old result overwriting a new one; lost notifications | Save an answer only if the attempt is still the current one | `ReplaySafeEffect`, work models, `.atomic()`, `.request_work()` |
| [3. Keep an external view current](projection-tutorial.md) | A search index stuck on an old value | Repair a search entry after an old write arrives late | `Projection` |
| [4. Schedule, move and cancel work](scheduling-tutorial.md) | Work running at the old time, or after cancellation | Publish text at a deadline, change it, cancel it | `ScheduledAction` |
| [5. Preserve an uncertain outcome](external-operation-tutorial.md) | Double charges after a timeout | Record an order that might have happened, without resending it | `DurableExternalOperation` |
| [6. Resolve uncertainty by reading](observation-tutorial.md) | Not knowing whether the provider acted | Ask the provider what happened and finish from its answer | `ObservedExternalOperation` |
| [7. Recover cleanup independently](compensation-tutorial.md) | Temporary output left behind by a crash | Record cleanup before creating output, finish it after a crash | `IndependentCompensation` |
| [8. Compose and test your work](composition-tutorial.md) | Multi-step work losing a step; untested recovery | Chain two steps, run work inline, generate failure tests | `.complete_or_defer()`, `.wake()`, `effect_contract_suite` |

Chapter 1 uses only Django's standard task API. Chapters 2 onwards introduce
Due's own classes, which you need when the work belongs to a row in your app that
can change while the work is running.

## How to read the code in this tutorial

Every code block mixes three kinds of names. Telling them apart is most of the
work of reading it:

| Kind | How to recognise it | What the tutorial does with it |
| --- | --- | --- |
| **Django and Python** | Imported from `django`, `typing`, the standard library | Assumes you know it (models, `transaction.atomic()`, `OneToOneField`) |
| **The example application** | Imported from `examples.…` or defined in the block | Shows it in full; it is what you would replace with your own code |
| **Due** | Imported from `due.django.public` (application code) or `due.django.conformance` (test tools) | Explains it in a sentence before its first use, then lists it in a short table after the block |

Where a Due name first appears in a code block, a one- or two-line `#` comment
says what it does or what to notice, and points back to the prose around it.
The example files themselves are not annotated, so they read slightly shorter.

A name is introduced once, in the chapter where it first appears. If a later
chapter uses it again without explanation, its meaning has not changed; the
[vocabulary page](vocabulary.md) has the short definition, and the
[API guide](api-guide.md) is the lookup table once you know the concepts.

Code from `due.django.conformance` is for exercises and tests only. The tutorial
uses it to simulate crashes and lost messages on purpose; your production code
never imports it.

## Prepare the native chapters once

[Chapter 1](quickstart.md) supplies the clone, dependencies and local PostgreSQL/
Redis setup. Complete it first. For chapters 2–6 and 8, use the prepared native
examples in the same checkout. Stop the greeting worker and Beat, then run:

```bash
docker compose up -d --wait
export DJANGO_SETTINGS_MODULE=examples.settings
uv run python -m django migrate
uv run python -m django due_check
uv run python -m django shell
```

If you already use dedicated PostgreSQL and Redis instances, keep the service
environment variables from chapter 1 instead of starting Compose again.

The models and classes shown in each chapter are already in the prepared example
files; the chapters show them so you can read them. Only the blocks introduced
as shell exercises are meant to be typed. You do not need to redefine the
classes or read the other installed examples.

Keep Celery workers stopped during the exercises. Each exercise drives the work
itself, so it can stop the work at an exact point, and a real worker running at
the same time would race it. The test driver it uses, `WorkTableDelivery`, runs
Due's real worker and recovery code against PostgreSQL; it only replaces the
clock and the queue. Chapter 2 introduces it.

To send ordinary requests through separate workers instead, run these in two
terminals with the same service settings:

```bash
uv run celery -A examples.celery_app:app worker -Q due --concurrency=1 --loglevel=INFO
uv run celery -A examples.celery_app:app beat --loglevel=INFO
```

Chapter 7 supplies the small additional setup for its temporary-output example.
Every native example has application contract tests, checked in CI. Chapter 8
explains how to build those tests for your own hooks.
