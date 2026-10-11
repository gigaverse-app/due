# Coming from Procrastinate

Procrastinate already gives you PostgreSQL-backed jobs, transactional deferral
and useful locking semantics. Consider Due when your application also carries
code to reject stale completion, reconcile uncertain provider requests, or finish
local state after the external operation succeeded. The migration should remove
that code and preserve its guarantees.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain—and when to stay

| Your application today | Benefit of adopting Due | Work you still own |
| --- | --- | --- |
| Task succeeds externally but crashes before publishing product state | Evidence-backed operations can finish local settlement from a retained receipt | A correlated provider result and receipt model |
| An older task publishes over a newer desired state | Captured revisions fence local publication; projection mode provides explicit repair policy | Remote ordering or replay-safe repair |
| Each task family has a stalled-job recovery protocol | Registered recovery owns work discovery, lease expiry and visible stopping | Policy, worker/scheduler operation and alerts |
| Reliability checks are repeated across tasks | Generated application histories exercise shared coordination | Real provider and business-result assertions |

Stay with Procrastinate when durable function execution and its lock semantics
already solve the problem. Due adds Django, Celery and a broker; it is not a
simpler PostgreSQL-only deployment. Transactional enqueue is shared ground,
not a new guarantee gained by switching.

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
job_id = publish.defer(document_id=document_id, text=text)

# After (with the Due example configured and the Document row already present):
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

The old job ID and the new work ID describe different lifecycles. Preserve a
separate old-to-new mapping if callers retain job IDs. Choose whether publication
may coalesce before changing the producer; do not use the document owner to
collapse an append-only series of required publications.

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

## Map the Procrastinate contract

First classify `.task`, `.defer[_async]`, `.configure`, retry configuration,
periodic tasks and all `lock`/`queueing_lock` uses. Procrastinate's supported
connection-bound deferral already offers atomic admission; preserve that property
when moving to Due's Django transaction.
[Source contract](https://procrastinate.readthedocs.io/en/stable/howto/production/external_connection.html).

| Procrastinate behavior | Due migration |
| --- | --- |
| Registered task and kwargs | Typed captured work plus a registered capability; retain the useful pure/provider helper |
| `.defer(...)` creates a job | Call the new application admission function inside the product transaction; choose occurrence versus desired-state identity |
| `.configure(connection=...)` | Move the product write onto Due's supported Django transaction, or stay on Procrastinate; opening another connection is not atomic |
| Execution `lock` | Preserve the shared serialization domain explicitly; per-work claims do not reproduce cross-task lock ordering |
| `queueing_lock` / `AlreadyEnqueued` | Decide whether the caller requires rejection or coalescing; ordinary `request_work` does not reproduce that exception contract |
| `schedule_at` / `schedule_in` | For a fixed delay, `pending.request_work(..., not_before=aware_datetime)`; use `ScheduledAction` if the deadline can move or become unnecessary |
| Periodic task | Persist each required occurrence, or deliberately define reconciliation as latest state; a Due recovery schedule is not the original cron job |
| Retry/stalled jobs | Translate exception classes, delays and budgets into Due policy; retain recovery for old jobs until drained |

For fixed delays, `not_before` controls admission of new work; repeating unchanged
input is not a general reschedule command. Use the schedule capability's
`schedule_at`/`cancel_pending` operations for mutable deadlines and handle their
`ALREADY_STARTED` result instead of claiming cancellation succeeded.

Do not infer strict external serialization from an owner lock or local revision
fence. A prior provider operation can outlive its lease. If all operations sharing
a Procrastinate lock must execute in order, a migration that merely creates
independent Due owners is incorrect. Stay on the old path until this requirement
has a proven replacement.

Start with an unlocked replay-safe task. Verify rollback, lost notification,
worker death, two identical submissions, and two changed submissions before
porting locked or non-repeatable tasks. Keep old workers and stalled-job recovery
for old IDs; do not let both engines retry an imported occurrence.

## Cut over and retain a way back

Deploy the Due schema, registrations, workers and recovery before changing the
selected producer. Prefer routing only new occurrences to Due while existing
Procrastinate jobs drain. Inventory queued, doing, scheduled, failed and stalled
jobs, including jobs waiting behind a shared lock. Preserve task names and
stalled-job recovery for that cohort.

If copying jobs is unavoidable, quiesce their producers, execution and retry
paths first. Keep a unique mapping of old job IDs and original provider keys;
an in-flight request is not safe to import as executable work merely because its
worker stopped. Keep unknown outcomes with their existing evidence owner until
a domain-specific transfer is proven.

To roll back, route new requests back to Procrastinate while Due completes what
it already owns. Do not give both engines permission to retry the same operation.
Retire old rows and routes only after required result lookups and redeliveries
are accounted for.

## Could you keep `.defer()`?

A facade for explicitly bound task names and keyword arguments is feasible, but
none ships today. Reject unsupported options before admission. `lock`,
`queueing_lock`, `AlreadyEnqueued`, priority and `connection=` need their actual
semantics preserved; they are not aliases for Due's per-work claim or revision.
An integer return value alone does not provide Procrastinate's job-manager API.

The facade is useful if it leads to a native capability with an explicit external
call, settlement and replay/evidence policy. It is a dead end if it keeps those
boundaries hidden inside an unchanged function or reconstructs Procrastinate's
lock engine just to preserve spelling.

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

Also test cross-task lock ordering, queueing-lock rejection, scheduled jobs
blocking the same lock, and two identical submissions. A migration that passes
Due's normal claim tests can still break these Procrastinate contracts.

## Sources

[Deferral](https://procrastinate.readthedocs.io/en/stable/howto/basics/defer.html),
[execution locks](https://procrastinate.readthedocs.io/en/stable/howto/advanced/locks.html),
[queueing locks](https://procrastinate.readthedocs.io/en/stable/howto/advanced/queueing_locks.html),
[stalled recovery](https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html).
