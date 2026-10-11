# Coming from Celery

Keep Celery. Due uses it to execute work and adds a durable obligation lifecycle
around selected business operations. This migration replaces task-specific
outbox, claim, reaper and settlement code; it does not replace the Celery worker
fleet or require moving all tasks at once.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## Where adoption pays off

If a product row commits and `.delay()` never reaches the broker, a post-commit
callback alone cannot recover the missing request. Due admits the work with the
product and periodically rediscovers it. If an old worker returns after a newer
revision, fenced settlement prevents it publishing current local completion.
Evidence-required modes separately handle the case where the provider may have
acted but the reply was lost.

These are useful when your tasks already need custom recovery and coordination.
Keep independent computation and workflows that rely on Canvas or result backends
on their existing paths. Celery already provides retries, routing and scheduling;
Due adds a particular product lifecycle, not universally better task execution.

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
result = publish.delay(document_id, text)

# After (with the Due example configured and the Document row already present):
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

The producer now returns persisted work rather than an `AsyncResult`. An HTTP
endpoint should report acceptance after commit unless its contract requires
completion. A caller that needs the published key must observe the product's
completion state; successful Celery message handling is not the business result.

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

## Map the Celery contract

Keep the Celery deployment. Register Due's runtime tasks and generated recovery
schedules in every relevant process. Move the chosen task's retry/reaper/claim
mechanism into Due and leave its reusable provider helper outside the task
wrapper. Do not let both Celery autoretry and Due schedule independent retries
for the same obligation.

Replace `on_commit(lambda: task.delay(...))` with transactional work admission;
the post-commit Due wake is an optimization backed by recovery. Preserve unrelated
Beat entries. A periodic recovery tick does not implement a business cron event.
`acks_late`, revocation and retries do not establish provider nonapplication.
[Celery task contract](https://docs.celeryq.dev/en/stable/userguide/tasks.html).

Chains, groups, chords and `AsyncResult.get()` require explicit redesign or stay
on Celery. Prefer passing a durable application occurrence ID to a thin legacy
entry point during transition; it should request progress on an already-admitted
obligation, not admit a second independent operation. Retain old task names while
messages can still arrive.

| Existing setting or behavior | Migration decision |
| --- | --- |
| `retry`, `autoretry_for`, backoff | One owner schedules retries: the Due capability policy |
| ETA/countdown | Fixed admission delay uses aware `not_before`; mutable deadlines use `ScheduledAction` |
| Revocation/termination | Local cancellation cannot prove an already-sent provider request did not act |
| Per-call queue/priority | Due's registered route is fixed per work model; do not silently discard options |
| Success callback sends another task | Admit successor work in fenced settlement |
| Chain/group/chord | Preserve Canvas or design explicit phase state and handoff; `Dependent` is not Canvas |
| Result backend/expiry | Keep old lookup for old IDs; store new domain results with deliberate retention |

## Cutover and rollback

Deploy registrations and recovery before redirecting one producer. Run legacy
messages under their existing task names until drained; Due and ordinary Celery
tasks can coexist in the same deployment. Disable the old recovery/retry path
only for obligations now owned by Due. Keep unrelated Beat entries intact.

Never dual-publish the old effect and new Due work to compare results. Shadow
read-only decisions instead. If an old task becomes a compatibility entry point,
it must locate a stable admitted occurrence and request progress, not create a
new occurrence each time it is redelivered.

Rollback redirects new admissions while Due continues to own already-committed
work. Keep its schema, worker routes, evidence and recovery until that work is
resolved. An old provider request with uncertain outcome must not be restarted
with a newly generated idempotency key.

## Should you keep `.delay()` through a shim?

Keeping a few producer entry points can reduce call-site changes. It does not
provide AsyncResult, Canvas, task request context or revoke semantics. No Due
Celery-API shim ships. Since Celery is already the transport, a clearly named
application admission function usually makes the new transaction boundary easier
to understand than another general decorator.

The transition remains useful if the native capability is accessible and old
producer methods simply delegate to it. It stops helping when compatibility
requires a second retry authority or hides UNKNOWN from operators.

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

Also prove that legacy redelivery cannot create a second occurrence, that only
one retry owner is active, and that successor admission survives a crash after
parent settlement. Exercise any retained result or Canvas consumers unchanged.
