# Coming from django-tasks-db

This migration changes the execution backend behind a Django task application.
The database backend already stores tasks through Django's ORM; Due is worth
considering when the task also needs explicit product completion, revision or
provider-evidence semantics. It is not a shortcut to a more lightweight queue.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
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

Due currently requires **Python 3.14+, Django >=6.0.8,<7, PostgreSQL, Celery,
a broker, workers and periodic recovery**. It is alpha; pin a reviewed GitHub
revision. Product, work and evidence must use the same writable database through
Django's default connection.

Choose the capability by the external contract: `ReplaySafeEffect` for safe
repetition, `Projection` for mutable external state, `ScheduledAction` for a
movable deadline, or `DurableExternalOperation` / `ObservedExternalOperation`
when a possibly applied request must wait for evidence. See the
[capability imports and examples](../capabilities.md); none makes an arbitrary
provider call safe to repeat.

1. Add `due.django` to `INSTALLED_APPS` and apply its migrations.
2. Define a product owner and concrete work model derived from
   `UniverseDurableWork`, with captured input fields and `durable_work_schema`.
   Apply your app's migration too.
3. Implement the external operation and its local completion separately. Register
   the capability in web, worker, Beat and management-command processes.
4. Include `due.django.runtime.tasks` in Celery imports, merge the generated
   recovery schedules, and run workers for the registered queue. Monitor overdue,
   stopped and UNKNOWN work, plus the recovery heartbeat.

The [integration reference](../integration.md) has the exact wiring. The
[document example](../../examples/documents/effects.py), its
[models](../../examples/documents/models.py) and
[registration](../../examples/documents/apps.py) form a complete implementation;
reuse that structure instead of inventing a queue wrapper. The `examples` package
belongs to this repository, not the installed Due runtime. In your application,
implement the corresponding producer and models in your own app.

## Follow one operation from submission to completion

For document publication, the producer change looks like this. The first line
uses your existing application task; the second uses Due's runnable example:

```python
# Before (your existing task and configured source client):
result = publish.enqueue(document_id, text)

# After (with the Due example configured and the Document row already present):
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

The old task is configured for the database backend. Today you must change
selected producers explicitly: Due does not supply a `TASKS` backend setting.
Consumers of TaskResult need the new canonical outcome, or a deliberately
implemented projection of their result contract. A work row is not TaskResult.

The destination commits the new document text and its obligation together.
`perform_effect` writes a revision-specific immutable object;
`finalize_success` publishes its key under Due's ownership/revision check.
Putting the old function wholesale in `perform_effect` would leave its embedded
DB updates outside that check. Completion callbacks do local DB work only;
required external follow-ups become separately admitted obligations.

The example owes the **current publication**: equal input preserves the existing
outcome, while changed input starts a revision. If every submission must execute,
create a distinct persisted request owner per occurrence instead. `work.pk` is
an obligation identifier, not a source-library result handle.

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

Django's backend extension point is promising, but a faithful adapter must
persist invocation identity and result metadata, validate options and accurately
project statuses. It is more than changing `BACKEND`. No Due backend ships.

An adapter is a useful path if native capabilities and richer obligation state
remain accessible. A generic four-status result that hides uncertainty would
limit the very advantage this migration seeks. The frontend guide contains the
optional [backend design](django-tasks.md#could-you-keep-the-standard-task-interface).

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
