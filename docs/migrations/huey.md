# Coming from Huey

Huey's compact task, result and scheduling API is a good fit for many applications.
Consider Due for selected tasks that have grown their own recovery and product
state coordination. Keep the rest on Huey; a task queue does not need replacing
simply because a richer lifecycle is available.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due can record the product change and its obligation in one PostgreSQL
transaction, recover a lost wakeup and fence local publication by captured
revision. Scheduled-action and evidence-required modes can replace task-specific
reschedule/revoke or uncertain-provider protocols when those semantics fit.

Huey already offers retries, scheduling and result handles. Stay if those are
sufficient, or if its lightweight deployment/storage choices are important.
Due adds Django/PostgreSQL, concrete work models, Celery and a broker; that cost
needs a demonstrated product reliability benefit.

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
result = publish(document_id, text)

# After (with the Due example configured and the Document row already present):
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

Here `publish` is the existing Huey-decorated task: calling it submits work.
After migration, `request_publication` performs local transactional admission.
Code that calls the returned Huey Result to wait for a value must be changed to
observe the specific product/request outcome; the Due work object is not callable.

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
| Decorated task invocation | Explicit transactional producer and captured work |
| Result handle / result group | Canonical product output or per-occurrence result; preserve consumers deliberately |
| Retries / retry delay | Due policy plus explicit terminal and uncertain outcomes |
| `.schedule()` | Aware fixed delay or a native scheduled action with eligibility checks |
| Revoke/restore | Define cancellation meaning; pending cancellation cannot unsend external I/O |
| Task locks | Identify the serialization domain; per-work ownership is not a global lock |
| Periodic task / pipeline | Keep Huey scheduling or persist occurrences and successor obligations explicitly |

## Pay particular attention to

A canceled task and an operation proven not to have happened are different
facts. When moving a revoke-based deadline to Due, handle an already-started
operation visibly rather than returning a false promise of cancellation.

Do not retain a required external follow-up only in a success callback. Admit it
as another obligation in local settlement. Likewise, cleanup in `finally` does
not survive a killed process; use persisted compensation when cleanup is owed.

## Cutover and rollback

Deploy Due schema, registration, workers and recurring recovery before moving
one producer. Drain queued, scheduled and retrying Huey tasks, retain result
storage for existing handles, and keep periodic production with exactly one
owner. Account for revoked tasks whose restore policy still makes them relevant.

Import only through a reviewed occurrence mapping with the old execution path
quiesced; preserve provider identity and unresolved outcomes. Roll back new
admissions independently while Due resolves its existing cohort. Do not execute
both paths to compare their side effects.

## Can a thin adapter help?

A bound task-call or fixed-delay facade can help a few call sites; none ships.
Result objects, revoke/restore, locks and pipelines are separate contracts,
not consequences of copying the decorator spelling. Keep the native capability
visible so richer scheduling, evidence and completion can be adopted. If the
facade must reproduce Huey's whole result/scheduler model, direct call-site
migration is likely easier to maintain.

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

Exercise Result consumers, lock contention, delayed-time handling, revoke versus
already-started execution, periodic occurrence identity, pipeline handoff and
worker death during cleanup.

## Sources

[Huey guide](https://huey.readthedocs.io/en/latest/guide.html), [recipes](https://huey.readthedocs.io/en/stable/recipes.html).
