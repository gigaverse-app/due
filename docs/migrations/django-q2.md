# Coming from Django Q2

Django Q2 already integrates workers, schedules, results and administration with
Django. Consider Due when selected tasks have accumulated custom obligation
recovery or provider reconciliation. The goal is to simplify those operations,
not to replace a satisfactory Django task system wholesale.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
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
task_id = async_task("myapp.tasks.publish", document_id, text)

# After (with the Due example configured and the Document row already present):
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

The function path above is an application task, not a Due import. Replace
callers of Q2 result lookup with the required canonical outcome or an explicit
per-occurrence result model. Preserve the tenant and arguments captured by the
old task instead of relying on whichever request context a worker happens to have.

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
