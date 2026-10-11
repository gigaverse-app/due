# Coming from django-tasks-rq

django-tasks-rq combines Django's task frontend with RQ execution. You have two
contracts to preserve: TaskResult and task options at the frontend, plus RQ
storage, delivery and operations underneath. Due adoption should improve one
selected product lifecycle without pretending both contracts disappear.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due can move product/work admission into one PostgreSQL transaction and recover
a lost Redis/Celery wakeup. Captured revisions and evidence-required operation
modes can replace custom stale-completion and uncertain-effect handling.
These gains require native admission and a split external/local task body.

Stay on django-tasks-rq when the standard task API and RQ execution already meet
your needs. Due has no backend implementation for that API today, and adopting
it adds work models, migrations and Celery operation. It does not automatically
remove Redis or preserve standard result objects.

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

Find the configured backend alias and all consumers of its returned result.
The source call looks identical to other Django Tasks backends, but existing
result IDs resolve through RQ-backed storage. The Due producer changes that
contract explicitly; do not point those old IDs at a new backend alias.

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
| Django `Task` / `.enqueue` | Native application producer today; no Due backend setting exists |
| TaskResult / return value / errors | Canonical result or explicit projection, with old lookup retained |
| RQ-backed storage and worker | Retain for old task IDs and messages; add Due worker/recovery for new work |
| Backend alias and queue selection | Keep source routing while mapping selected work to a declared Due route |
| `run_after`, priority and context | Audit the installed backend’s support; do not silently drop requested behavior |
| Legacy bundled package path | Preserve historical schema/import compatibility during the package split |
| Retry and retention | One retry owner per occurrence; result TTL cannot remove unresolved evidence |

## Pay particular attention to

A framework task API does not make a Redis enqueue atomic with a PostgreSQL
product update. Move durable Due admission inside the product transaction;
leaving it in `on_commit` retains the gap before admission. If an existing outbox
already covers this, preserve its guarantees until the replacement is proven.

Keep frontend result IDs distinguishable by backend during coexistence. Native
UNKNOWN requires a richer obligation view; translating it into an ordinary
FAILED result can cause a consumer to repeat a possibly applied operation.

## Cutover and rollback

Deploy Due schema, capability registration, workers and recovery. Change only
new admissions for the selected task; keep the original backend alias and RQ
workers for old ready, started, deferred, scheduled and failed jobs. Retain task
code and result lookups for outstanding frontend handles.

A transferred job needs a unique source-to-occurrence mapping, original provider
key and a stopped old retry path. Unknown effects are not fresh work. Rollback
changes new routing while Due remains responsible for its admitted cohort.
Do not remove the RQ backend or result storage until its callers have drained.

## Can a thin adapter help?

The strongest future route is a Django Tasks backend, rather than emulating
RQ internals behind it. That backend still needs real TaskResult fidelity,
invocation identity and unsupported-option validation; it is unimplemented.
A minimal named producer facade can help now only if callers deliberately accept
native results.

Keep access to native evidence, scheduling and fenced completion. The optional
[frontend backend design](django-tasks.md#could-you-keep-the-standard-task-interface)
explains why generic four-status tasks must not conceal uncertain operations.

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

Test frontend result refresh/errors and backend-aware lookup, RQ delayed/retry
cohorts, transaction rollback, two equal submissions, context/priority options
and rollback routing. Verify every old TaskResult still resolves to its original
execution owner.

## Sources

[django-tasks-rq](https://github.com/RealOrangeOne/django-tasks-rq), [Django Tasks API](https://docs.djangoproject.com/en/6.0/ref/tasks/), [RQ jobs](https://python-rq.org/docs/jobs/).
