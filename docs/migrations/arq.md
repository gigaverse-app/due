# Coming from arq

arq is built around async functions and Redis jobs. Due is a Django-backed
obligation runtime executed through Celery. Migrate only when a product lifecycle
benefit justifies that runtime change; an async application does not gain by
replacing its enqueue spelling alone.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due can tie admission to a Django product transaction, distinguish changing
inputs from execution attempts, and recover fenced local completion. Its
non-repeatable operation modes explicitly retain uncertain provider outcomes.
Those can remove application recovery code around an arq job.

arq already expects jobs may run more than once under its pessimistic execution
model. Due does not eliminate the replay problem for arbitrary async functions.
Stay with arq for an async-first worker, shared event-loop resources and Redis
job/result behavior that already meet the requirement.

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
job = await redis.enqueue_job("publish", document_id, text)

# After (with the Due example configured and the Document row already present):
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

This is a boundary translation, not a safe one-line edit inside an async view.
Run the product update and Due admission together in a synchronous transactional
callable reached through Django's supported async bridge. An awaited enqueue on
one context cannot join a separate ORM transaction implicitly.

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
| Registered async function name | Named capability retaining a compatible provider helper |
| Worker `ctx` / startup resources | Recreate typed worker-local dependencies; do not reuse an event-loop-bound client across processes |
| `_job_id` and uniqueness lifetime | Explicit occurrence key and retention; Due coalescing is a different contract |
| `_defer_until` / `_defer_by` | Normalize to aware `not_before`; scheduled-action mode for mutable deadlines |
| Job result / expiry | Persist required per-occurrence output; work/evidence retention is not result expiry |
| Retry and max tries | One Due retry authority, with tested counts and error disposition |
| Abort and cron | Specify pending cancellation and recurring identities separately; abort cannot retract provider acceptance |

## Pay particular attention to

The provider coroutine may be reusable, but the old worker's context lifecycle
is not transferred with it. Audit connection pools, HTTP clients, shutdown hooks
and cancellation behavior before choosing how to run it in the destination.
Due exposes `run_effect_coroutine` for a coroutine boundary; this is not an arq
worker or arbitrary event-loop compatibility guarantee.

If a job ID can be reused after its result expires, decide whether that means a
new business occurrence. Preserve original provider keys for existing work;
using a new Due-generated key can repeat an already-applied request.

## Cutover and rollback

Install Due and verify worker/recovery behavior independently first. Route new
occurrences of one function to it; let arq finish queued, in-progress, deferred
and retrying jobs with their existing contexts and result retention. Keep cron
ownership explicit during coexistence.

Quiesce old execution before any import, preserve job-to-occurrence mappings and
unknown provider outcomes, and do not move opaque runtime context into a payload.
Rollback changes new routing while Due remains responsible for admitted work.
Retain Redis results and old handlers until their consumers and delayed jobs drain.

## Can a thin adapter help?

A bound async admission facade is possible, but unimplemented. It could await
one transactional producer while rejecting unsupported arq options. It would not
provide WorkerSettings, `ctx`, abort or Redis Job semantics automatically.
A useful migration exposes native settlement/evidence; if all old async worker
semantics must remain invisible behind the facade, keeping arq is usually the
clearer choice.

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

Verify event-loop/resource lifetimes, coroutine cancellation after provider
acceptance, repeated job IDs across expiry, async result consumers and atomic
admission from the actual async request path.

## Sources

[arq 0.28 documentation](https://arq-docs.helpmanual.io/), [Due coroutine boundary](../../src/due/django/runtime/transport.py).
