# Learn Due, one capability at a time

Start with a function that sleeps, prints and returns a greeting. Then introduce
one new requirement per chapter. Each chapter defines the relevant hooks, shows
how to call them, and gives you an observable result.

The application operations stay deliberately small: arithmetic, text files, and
local SQLite records. These let you inspect Due's behavior without a provider
account, a network API or a substantial business application.

| Chapter | What you will do | API you will learn |
| --- | --- | --- |
| [1. Run and recover a task](quickstart.md) | Define a function, enqueue it, read its result, kill its worker and recover the job | `@task`, `.enqueue()`, `.get_result()` |
| [2. Own the result](native-work-tutorial.md) | Capture input and publish a result only while the attempt still owns completion | `ReplaySafeEffect`, `.for_owner()`, `.atomic()`, `.request_work()` |
| [3. Keep an external view current](projection-tutorial.md) | Write a search entry and repair it after an old write arrives late | `Projection` |
| [4. Schedule, move and cancel work](scheduling-tutorial.md) | Publish text at a deadline, change the time and cancel pending work | `ScheduledAction` |
| [5. Preserve an uncertain outcome](external-operation-tutorial.md) | Record an operation that might have happened even though its reply was lost | `DurableExternalOperation`, receipt storage, outcome callbacks |
| [6. Resolve uncertainty by reading](observation-tutorial.md) | Ask the provider what happened and settle from its original receipt | `ObservedExternalOperation` |
| [7. Recover cleanup independently](compensation-tutorial.md) | Record cleanup before creating temporary output and recover it after process death | `IndependentCompensation`, `OwnedDurableWorkCapability` |
| [8. Compose and test your work](composition-tutorial.md) | Hand off work in a transaction, try completion inline and bind application contracts | `.complete_or_defer()`, `.wake()`, `effect_contract_suite` |

Use `due.django.public` for application classes and setup, and
`due.django.conformance` for the common test drivers and contract bindings.
The [API guide](api-guide.md) is the compact lookup table once you know the concepts.

## Reading the code

The import path tells you who supplies a name:

| Import or declaration | Supplied by | How to use it |
| --- | --- | --- |
| `django.*` | Django | Familiar models, ORM operations, transactions and the task interface |
| `due.django.public` | Due | Application primitives; each new capability or helper is introduced in its chapter |
| `due.django.conformance` | Due's optional testing API | Controlled delivery, faults and contract tests; these are used in exercises, not required application hooks |
| `examples.*` or `acme.*` | This tutorial's example code | Models, functions and configured instances supplied in the checkout; they are not names to import into your own application |
| `time`, `typing`, `datetime`, etc. | Python's standard library | Ordinary Python operations and annotations |
| `pydantic`, `pytest`, `celery` | Other dependencies | Receipt data types, tests and worker transport, identified where used |

Names such as `Calculation`, `DoubleNumber`, `calculate` and `request_number` are
ours. Names such as `ReplaySafeEffect`, `for_owner` and `request_work` are Due's.
Chapter 2 introduces the shared concepts: the application record that owns work,
the work row holding its input, the capability that runs it, and the checks that
allow an attempt to save completion. Later chapters link back to those definitions
and explain their additional concepts at the point of use.

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

The chapters identify the small model and hook files that matter for their
exercise. Model and class definitions explain the supplied files; the steps
labelled as shell exercises import those configured implementations. You do not
need to redefine the classes or read the other installed examples. Keep workers
stopped during controlled exercises: those explicitly drive execution or use
`WorkTableDelivery` to advance the recovery clock. The driver uses Due's real
worker/recovery bodies against PostgreSQL.

To send ordinary requests through separate workers instead, run these in two
terminals with the same service settings:

```bash
uv run celery -A examples.celery_app:app worker -Q due --concurrency=1 --loglevel=INFO
uv run celery -A examples.celery_app:app beat --loglevel=INFO
```

Chapter 7 supplies the small additional setup for its temporary-output example.
Every native example has application contract bindings and executed-report
validation in CI. Chapter 8 explains how to build those bindings for your own
hooks.
