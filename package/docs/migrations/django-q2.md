# Coming from Django Q2

Django Q2 already integrates workers, schedules, results and administration with
Django. Consider Due when selected tasks have accumulated custom obligation
recovery or provider reconciliation. The goal is to simplify those operations,
not to replace a satisfactory Django task system wholesale.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due provides a shared lifecycle for captured inputs, stale-completion rejection,
uncertain effects and evidence-backed local repair. Atomic admission can improve
a product-to-broker boundary when it currently spans separate commits. The gain
depends on the actual Q2 broker configuration; do not claim a gap for an ORM
admission path already participating in the same transaction.

Stay with Q2 when its schedules, hooks, chains, broker choices and admin views
satisfy the workload. Due adds Celery, its broker and concrete work tables, and
does not preserve those Q2 surfaces automatically.

## Prepare the Due side

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


## The interfaces you will work with

| Familiar primitive | Its strength | Due counterpart and semantic change |
| --- | --- | --- |
| `async_task()` and import-path functions | Lightweight Django dispatch with broker choices | Explicit typed capability and producer |
| `Task`, `result(task_id)`, hooks | Persisted results and completion hooks | Work state plus `finalize_success` in fenced local settlement |
| `q_options`, schedules, chains/groups | Per-task execution options and orchestration | Policy and domain-owned successors; no chain/schedule importer |

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
from django_q.tasks import async_task, result
from .models import Document
from .storage import write_revision


def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_revision(text, publication_id)
    Document.objects.filter(pk=document_id).update(published_key=key)
    return key


task_id = async_task(publish, 42, "Hello", "request-123")
# Later; None can mean not ready, so also inspect task status.
key = result(task_id)
```

A Q2 hook receives the completed task, including its result. The comparable
Due completion method receives captured work and the provider result, and runs
under the validated owner/work fence. Move only local DB consequences into it;
external follow-ups need their own admitted obligation.

Do not translate `timeout` and broker `retry` into identical Due numbers. They
control different retry/visibility boundaries. The gain is a persistent product
obligation and explicit local settlement; the atomic-admission comparison depends
on whether your current Q2 broker already uses the same database transaction.

## After: model the obligation, split execution from completion

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


## Translate the contracts, not just the call

| Existing concept | Due migration decision |
| --- | --- |
| `async_task` / `AsyncTask` | Explicit producer and concrete captured work |
| `q_options` | Inventory routing, timeout, hooks and result options; reject unmapped values |
| Broker selection, including ORM | Verify old transaction/delivery semantics; Due uses its documented Django/Celery path |
| Retry, timeout and acknowledgement | One retry authority with a declared replay/evidence policy |
| DB or cached results | Preserve lookup lifetime and required output independently of work retention |
| Hooks / chains / groups | Local consequences in settlement; external successors as durable obligations |
| Scheduled and missed occurrences | Define catch-up and occurrence identity; recovery cadence is not business cron |

## Pay particular attention to

Q2 broker choice affects what a message acknowledgement means. Audit the actual
configuration rather than treating all Q2 deployments as Redis queues. Due
cannot retroactively repair a lost pre-cutover obligation unless durable product
facts still identify it.

A hook that sends the next task must become a durable handoff if the next effect
is required for product completion. Marking the parent successful before that
handoff commits would preserve the very gap the migration is meant to remove.

## Cutover and rollback

Run Due migrations, registrations, workers and recovery before moving a selected
producer. Let Q2 complete its old broker entries and chains; retain DB/cache
results for old task IDs. Inventory running, queued, failed and scheduled work,
including missed schedules whose catch-up policy still owes occurrences.

Disable only the migrated schedule producer, and only once the new owner is live.
Imports require a quiesced execution/retry path and stable occurrence/provider
mapping. Rollback redirects new requests; Due keeps its admitted obligations,
evidence and recovery until resolved. Keep Q2 admin access while old work remains.

## Can a thin adapter help?

A bound `async_task` facade may be practical, but no adapter ships. It cannot
promise all `q_options`, broker modes, hooks, cached results and schedules from
the function signature alone. Keep native capability access and reject unsupported
options early. Reproducing Q2's whole worker/admin/results system would make the
facade a second platform rather than a small migration aid.

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

Check the selected broker's transaction boundary, chain/hook failure, cached
result expiry, timeout/retry interaction, missed-schedule catch-up and old-ID
lookup. Prove one execution owner per accepted occurrence during rollback.

## Sources

[Django Q2 documentation](https://django-q2.readthedocs.io/en/master/), [task API](https://django-q2.readthedocs.io/en/master/tasks.html).
