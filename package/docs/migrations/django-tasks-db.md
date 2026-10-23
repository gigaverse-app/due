# Coming from django-tasks-db

This migration changes the execution backend behind a Django task application.
The database backend already stores tasks through Django's ORM; Due is worth
considering when the task also needs explicit product completion, revision or
provider-evidence semantics. It is not a shortcut to a more lightweight queue.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due can centralize worker-death recovery, stale product publication and receipt-
backed completion repair that you would otherwise implement around individual
tasks. Its work row records a business obligation through multiple attempts,
and its generated contracts can exercise the application's actual boundary.

Do not claim new transactional admission merely because Due also uses the ORM.
Verify the current backend/database route: if its insert already shares the
product transaction, retain that property. Stay on django-tasks-db for simple
function tasks and TaskResult, especially when avoiding Celery and a broker is
part of your deployment choice.

## Prepare the Due side

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


## The interfaces you will work with

| Familiar primitive | Its strength | Due counterpart and semantic change |
| --- | --- | --- |
| `Task` / `@task`, `.enqueue()` | A standard task frontend independent of backend choice | A domain capability; Due does not yet implement `BaseTaskBackend` |
| `TaskResult`, `.refresh()`, `.return_value` | Typed task identity, status and result lookup | `PublicationWork` tracks obligation state; `Document` owns the business result |
| `.using(run_after=..., priority=...)` | Per-enqueue options where the backend supports them | `not_before` for new work; priority and arbitrary routing are not equivalent supported options |

## Before: the task and its caller

The comparison publishes document 42's text to an immutable object and records
its key in `Document.published_key`. The source example's application helper
`write_revision(text, publication_id)` writes the same bytes to a stable key on
retries and returns that key. `publication_id` is allocated once per request,
not on each retry. This helper is application code, not a library API; the Due
[shared implementation](../migrations.md#shared-native-implementation) shows the file-write body.

These are focused integration excerpts: put definitions in importable app
modules, configure the source worker/backend, and provision the `Document` row.
The deliberately simple source task has no application-specific stale-write
fence. If yours already has one, preserve its contract and compare what code Due
would replace; the source library is not inherently unable to implement it.

```python
from django_tasks import task
from .models import Document
from .storage import write_revision


@task
def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_revision(text, publication_id)
    Document.objects.filter(pk=document_id).update(published_key=key)
    return key


task_result = publish.enqueue(42, "Hello", "request-123")
# Later, once the backend reports SUCCESSFUL:
task_result.refresh()
key = task_result.return_value
```

The example uses the `django_tasks` frontend with the database backend selected
in `TASKS`. Changing `TASKS` to a Due backend is not currently possible: none
ships. Port selected task definitions and callers as shown below, and keep
`db_worker` running for the old cohort.

If backend task inserts already share the product transaction/database, atomic
admission is an existing strength. The gain to assess is moving stale-completion
checks and recovery into the capability. The cost is adding Celery and a broker,
and replacing `TaskResult` consumers with domain outcomes.

## After: model the obligation, split execution from completion

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


## Delayed delivery versus a movable deadline

With a timezone-aware `deadline`, the familiar delayed call is:

```python
publish.using(run_after=deadline).enqueue(42, "Hello", "request-123")
```

For a fixed initial delay, change the final line **inside the Due producer's
existing `atomic()` block** to:

```python
return pending.request_work(
    PublicationWork(text=text, universe="example"),
    not_before=deadline,
)
```

That sets when new work becomes eligible; delivery can happen later. Sending
unchanged inputs again is not a command to move an existing deadline. When the
product concept is “do this at a deadline that may change,” declare a separate
`ScheduledWork` model and `ScheduledAction` with `is_still_needed`,
`perform_effect` and `on_completed` hooks. Its policy requires `retention=None`;
explicit `input_fields` must include `run_at` and `cancelled`.
[Declaration example](../../tests/declarations/test_simple_callbacks.py).

For that registered capability, the native calls have meaningful result types:

```python
from due.django.capabilities.scheduled import CancellationResult, ScheduleResult

# deadline_action and DeadlineWork are your declared capability and work model.
status: ScheduleResult = deadline_action.schedule_at(
    42,
    DeadlineWork(universe="example"),
    at=deadline,
)
cancellation: CancellationResult = deadline_action.cancel_pending(42)
```

Handle `ALREADY_STARTED` from either operation: Due cannot promise prevention
once an attempt may have escaped. `UNCHANGED` from scheduling and
`NOT_SCHEDULED` from cancellation are distinct outcomes too. The useful gain is
an explicit, persisted deadline/cancellation contract; this does not reproduce
the source library's general revoke, cron or priority API.

## Translate the contracts, not just the call

| Existing concept | Due migration decision |
| --- | --- |
| `@task`, `.enqueue` and `.using` | Native admission for selected tasks; leave other backend aliases alone |
| ORM-backed task/result row | New concrete domain owner/work models; no generic row importer exists |
| Return value, errors and refresh | Persist and expose the required domain result; preserve old TaskResult lookup |
| `run_after` / priority / queue | Aware due time where supported; explicitly retain or reject ordering/routing requirements |
| `db_worker` | Keep for old tasks; run Celery plus Due recovery for new obligations |
| Pruning completed task results | Separate legacy result retention from Due work and unresolved evidence retention |
| Older bundled backend paths | Inventory installed package versions and migration history before changing imports |

## Pay particular attention to

The current separate package uses `django_tasks_db.DatabaseBackend`; older
applications may still reference the backend bundled in django-tasks before the
split. Preserve historical migrations and inspect the installed schema rather
than assuming a fresh package rename migrates its data.

A result's four standard task statuses do not faithfully express Due's UNKNOWN
outcome. Do not turn UNKNOWN into FAILED and invite resubmission. Use the native
operation view for evidence-required work. A future standard backend can begin
with a restricted replay-safe profile, but that integration is not available now.

## Cutover and rollback

Apply Due and application migrations, start registered recovery/workers, then
move new occurrences of one task. Keep `db_worker`, its task definitions and
result lookup for old rows. Include ready, running, deferred and failed tasks in
the inventory; a completed task may still have a required callback obligation.

Do not copy old rows into READY Due work while old workers can claim them.
Imports need a unique occurrence mapping, preserved provider keys and explicit
handling of uncertain effects. Rollback routes future admissions back while Due
resolves its accepted cohort. Retain evidence and both lookup paths as required.

## Can a thin adapter help?

Django's backend extension point is supported by Due's optional task app. It
persists invocation identity and result metadata, validates supported options,
and projects Due state to Django results. Deploy its migrations, registration,
worker and recovery schedule before routing new tasks to it.

Native capabilities and richer obligation state remain accessible alongside the
adapter. A generic four-status result cannot represent uncertain effects; those
belong in native evidence-aware capabilities. See the
[backend setup guide](../django-tasks.md).

## Prove the migration before switching traffic

Bind [Due's application contracts](../conformance.md) to the actual new producer,
provider helper and completion path. Verify transaction/savepoint rollback,
lost post-commit notification, duplicate delivery, death after the external
call, stale completion after changed inputs, and visible retry exhaustion.
Observe the product result as well as the work row. Library tests alone cannot
prove provider idempotency or your application's completion condition. Include
two equal admissions with the intended occurrence/coalescing behavior, an
authorized retry of stopped work distinct from ordinary resubmission, and one
poisoned recovery identity that must not stall other work. If speed or operating
cost motivates the move, measure the real workload before and after; moving the
same effect to a different engine is not performance evidence.

Prove rollback on the actual database route, result refresh and exception
behavior, provisional IDs after rollback, two identical invocations, deferred
execution and coexistence with result pruning. Compare the required priority
behavior before accepting a new backend.

## Sources

[django-tasks-db](https://github.com/RealOrangeOne/django-tasks-db), [Django Tasks API](https://docs.djangoproject.com/en/6.0/ref/tasks/).
