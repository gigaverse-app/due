# Migrating to Due

Jump to [Procrastinate](#porting-from-procrastinate), [RQ](#porting-from-rq-django-rq-or-django-tasks-rq),
[Django Tasks](#porting-from-django-tasks--django-tasks), [Celery](#porting-from-celery),
[DBOS](#porting-from-dbos), [other libraries](#porting-from-other-libraries), or
[cutover](#cutover-without-losing-or-duplicating-work).

Start with one business operation whose recovery or completion semantics need
improvement. Leave unrelated jobs on their current engine. The
[competitor audit](competitors.md) explains when migration is worthwhile;
[compatibility assessment](compatibility.md) distinguishes a producer facade
from a compatible execution engine.

These instructions target Due at `65e0bcf`, reviewed on 2026-10-11. **No listed
compatibility adapter ships today.** Examples below use Due's existing document
application; substitute your models and provider contract deliberately.

## Before choosing an API

The current package needs Python 3.14+, Django >=6.0.8,<7, one writable
PostgreSQL database on Django's default connection, Celery, a broker, workers
and periodic recovery. It is alpha and installed from GitHub; pin a reviewed
revision. It is not a framework-neutral queue and cannot join an arbitrary
psycopg/SQLAlchemy transaction. See [installation](../README.md#install) and
[integration](integration.md).

Inventory each operation before porting it:

| Existing behavior | Required decision |
| --- | --- |
| Two identical enqueue calls | Two occurrences, or one desired state? Do not silently deduplicate occurrences. |
| Function rereads its object at execution time | Should Due capture the input now, or intentionally read current facts later? |
| Function performs several calls and DB writes | Which calls can repeat? Which writes belong in fenced settlement? |
| Retry, timeout, cancellation | Which outcomes prove nonapplication? Which remain uncertain? |
| Result handle, callbacks, dependencies | Which callers require a value or a durable handoff? |
| Queue, priority, ordering, tenant | Which are correctness requirements rather than scheduling preferences? |
| Old jobs and receipts | Who owns each outstanding occurrence during and after cutover? |

Choose `ReplaySafeEffect` for safe repetition, `Projection` for current external
state, `ScheduledAction` for movable/cancellable eligibility, or
`DurableExternalOperation`/`ObservedExternalOperation` for uncertain occurrences.
Use the [capability examples](capabilities.md) for declarations, evidence storage
and imports. A payment function does not become replay-safe by changing its
decorator, and a local fence cannot retract an earlier provider call.

## A concrete destination for a queued function

The existing runnable example supplies the complete models, migrations,
capability, registration and worker setup:

- [Document and PublicationWork](../examples/documents/models.py).
- [External write and fenced product publication](../examples/documents/effects.py).
- [Registration](../examples/documents/apps.py) and [Celery app](../examples/celery_app.py).

Its admission path is usable as written after the example setup:

```python
from examples.documents.effects import request_publication
from examples.documents.models import Document

document = Document.objects.create()
work = request_publication(document.pk, "New text")
```

`request_publication` changes the document and records captured text in one
transaction. The worker writes an immutable revision object, then publishes its
key under Due's ownership fence. Reading `work.pk` identifies persisted work;
it is **not** a Celery AsyncResult or RQ Job. Refresh the product/work row to
inspect completion; use [operator tooling](operations.md) for stopped work.

For illustration, these are equivalent *producer migration intentions*, not
interchangeable return contracts. Names other than the example's
`request_publication` are application-defined old tasks:

| Source call | Destination call for this example | Contract to port separately |
| --- | --- | --- |
| Procrastinate `publish.defer(document_id=pk, text=text)` | `request_publication(pk, text)` | Job ID, locks and `.configure()` options |
| RQ `queue.enqueue(publish, pk, text)` | `request_publication(pk, text)` | `Job`, results/TTL and dependencies |
| Django Tasks `publish.enqueue(pk, text)` | `request_publication(pk, text)` | `TaskResult`, task context and backend routing |
| Celery `publish.delay(pk, text)` | `request_publication(pk, text)` | `AsyncResult`, retries, ETA and Canvas |
| DBOS queued publication workflow | `request_publication(pk, text)` only if it is one bounded operation | Workflow identity, checkpoints, results and durable waits |

**Identity changes matter.** The example keeps one work row per document:
unchanged input preserves the existing outcome/budget, and changed input becomes
a revision. It is suitable when the product owes the current publication. If
all historical publications must occur, create a distinct persisted publication
request owner per occurrence and a work row for each. Preserve the old request
ID in that owner with an appropriate uniqueness constraint; don't force an
append-only job stream through a latest-state work row.

Separate the old task body into external execution and local completion. For
replay-safe work, use `perform_effect` and `finalize_success`; the latter must do
only transactional DB work. Required notifications or successor operations
become new durable obligations admitted in settlement, not external I/O in the
callback. For evidence-required work, follow `finalize_applied` and the canonical
receipt-store example instead. Never wrap a whole legacy task and assume its
internal database writes inherit Due's finalization fence.

## Porting from Procrastinate

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

## Porting from RQ, django-rq or django-tasks-rq

Inventory `Queue.enqueue[_at/_in]`, callable import paths, custom serializers,
`job_id`, `Retry`, callbacks, dependencies, `Job.meta`, result/failure retention
and cancellation. RQ's Redis job record is not Due's work row. A Redis pipeline
cannot join a PostgreSQL product transaction.
[Queue API](https://python-rq.org/docs/),
[jobs](https://python-rq.org/docs/jobs/),
[retry behavior](https://python-rq.org/docs/exceptions/).

Replace selected producers with the admission function above. Persist arguments
as declared model fields or a deliberately validated payload; do not blindly
unpickle old job payloads into a new generic executor. Put results required by
callers in canonical application state during settlement. Port a callback that
performs an effect to another obligation, and a dependency to an explicit
completion condition/handoff. `Dependent` wakes existing work; it does not create
RQ's dependency/result graph automatically.

Normalize deferred times explicitly: RQ accepts naive local datetimes, whereas
Due's `not_before` requires an aware datetime. Preserve the original intended
timezone when translating, especially across daylight-saving transitions.
[RQ scheduling](https://python-rq.org/docs/scheduling/).

Translate execution timeouts to provider timeouts and Due's registered worker
limits, with the lease longer than the hard worker limit. Translate retry counts
by tested histories, not matching numeric names: Due distinguishes delivery
reservation recovery from failed execution. Result TTL and the obligation's
retention have different meanings; retain uncertain evidence independently.

Drain the selected RQ queue/registry cohort before switching ownership. Scheduled,
deferred, failed and started jobs all need a disposition, not just the ready queue.
Keep old result lookup alive for IDs already returned to clients. Redis may still
be needed as Due's Celery broker, so this migration does not necessarily remove it.

## Porting from Django Tasks / django-tasks

Determine both the import (`django.tasks` versus `django_tasks`) and the installed
backend. Current backport versions separate database and RQ workers into
`django-tasks-db` and `django-tasks-rq`; older deployments may have bundled them.
Their storage/migration history cannot be replaced by changing one setting.
[Backport](https://github.com/RealOrangeOne/django-tasks),
[DB backend](https://github.com/RealOrangeOne/django-tasks-db).

**Available path today:** replace selected `.enqueue()` producers with explicit
Due admission and migrate their task bodies as described above. Keep other
backend aliases intact. The source function's return value, error list, attempts,
worker IDs and refreshable result are consumer contracts to preserve or explicitly
change. The example Due publication returns a work row, so it is not a drop-in
replacement for a caller expecting `.return_value`.

**Possible future path:** implement the [backend proposal](compatibility.md#django-tasks-backend-proposal)
and retain `@task`/`.enqueue()` for its supported subset. No `TASKS` configuration
for Due is provided here because no such backend is implemented.

When migrating from an enqueue performed in `transaction.on_commit`, move durable
admission **inside** the product transaction. Leaving it in the callback still
permits the process to die after committing the product but before admitting
work. Conversely, do not claim to fix this gap for an existing database backend
that already inserts atomically on the same connection. For async request code,
run the product mutation and Due admission in one synchronous transactional
callable; an async enqueue wrapper alone does not join unrelated transactions.

## Porting from Celery

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

## Porting from DBOS

Inventory workflows separately from steps, datasource transactions, queue
configuration, workflow IDs, handles/results, sleep, events/messages, children
and code-version recovery. Current documentation uses datasource-based
transactions; match the API to your installed DBOS version.
[Workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial),
[datasources](https://docs.dbos.dev/python/tutorials/transaction-tutorial),
[queues](https://docs.dbos.dev/python/tutorials/queue-tutorial).

Port only a bounded operation after deciding that its saved execution history is
not required. An idempotent external step can become `perform_effect`; its local
product update becomes fenced settlement. A non-repeatable step needs explicit
attempt evidence and observation. Keep the original provider idempotency key for
an occurrence that already crossed the boundary; Due's newly generated key is
not a safe substitute for it.

A workflow containing A, then a durable wait, then B must not become one retried
function. Either retain DBOS, or deliberately model phase state, wait conditions,
outputs and atomic successor admission using product rows and separate
capabilities. That is a workflow rewrite, not a thin shim. Due provides neither
DBOS history import nor checkpoint replay.

Drain started workflows on their original code/version and route only new
operations to Due. For coexistence, a DBOS workflow may reference one stable
Due-owned occurrence and observe its product result; arrange durable, idempotent
admission at the boundary. Do not assert that a DBOS transaction and a separate
Django transaction commit atomically. Existing workflow completion must wait for
the Due-owned result, not merely successful submission.

## Porting from other libraries

These are bounded migration recipes, not claims of full API coverage. Each uses
the same native admission and cutover procedure; keep listed unmatched features
on the source engine until their replacement is explicit.

| Source | Port first | Preserve or redesign | Shim decision |
| --- | --- | --- | --- |
| Chancy | One `job`/`push` handler as captured work and a capability | Transaction cursor, uniqueness, queue rate/concurrency limits, plugins, executor selection, workflow dependencies | Named producer binding only; PostgreSQL-only simplicity is a strong reason to stay |
| PgQueuer | Decode the entrypoint's byte payload into a canonical work model | Async connection ownership, batching, serialized dispatch, execute-after and completion watcher | Small admission facade possible; not a driver/worker replacement |
| Dramatiq | Selected actor `.send()` calls into Due admission; retain provider helper | Middleware/context, result backend, pipelines/groups and actor retries | Conditional facade; no actor middleware emulation |
| Huey | Selected task invocation or `.schedule()` into native admission | Result reads, revoke/lock semantics, periodic tasks and pipelines | Conditional facade; result and scheduler compatibility are separate work |
| arq | Selected `enqueue_job` to one transactional producer; adapt provider coroutine at the execution boundary | Worker `ctx`, shared resources, job-ID uniqueness/expiry, abort, cron, async result behavior | Only an explicit subset; do not promise event-loop/worker compatibility |
| Taskiq | Selected `.kiq()` submissions into native admission | Broker plugins, dependency injection, middleware, labels and result backend | Demand-driven subset; plugin compatibility is not assessed here |
| Django Q2 | Selected `async_task` call into native admission | Broker mode, `q_options`, hooks, chains, cached/DB results and scheduled occurrences | Explicit producer facade only |
| Temporal | Isolate a bounded Activity's domain operation, if needed | Workflow history, replay, signals, timers, child workflows and cancellation | Interoperate or redesign; no SDK shim |
| PGMQ | Convert one message contract to captured domain work | Claim/visibility/ack/archive semantics, batch consumption and external consumers | Migrate consumer behavior, not SQL/client interface |

Official sources and inspected versions are in the
[competitor source register](competitors.md#source-register). In particular,
Chancy and PgQueuer already document transactional enqueue; do not sell this
migration as closing a gap they do not have.

## Cutover without losing or duplicating work

1. **Record the inventory.** Partition ready, running, delayed, retryable, stopped,
   successful and uncertain work by old engine, task name, tenant and occurrence
   ID. Include callbacks, child work and accepted operations without a queue row.
2. **Deploy the destination first.** Apply work/evidence schema, install all
   registrations, run workers/recovery, and verify telemetry before routing new
   admissions. Preserve provider IDs, tenant keys and payload version semantics.
3. **Choose one execution owner per occurrence.** Prefer letting old work drain
   on its original engine while new admissions use Due. Gate producer routing
   consistently so retries of an old request cannot create a new Due occurrence.
4. **If importing is unavoidable, quiesce that cohort.** Disable its old producers,
   schedules and execution/retry paths; account for in-flight remote calls before
   granting Due permission to execute. Record a durable old-ID to new-owner
   mapping with uniqueness and resumable progress. Do not import unresolved work
   as READY or reset its provider idempotency key. There is no generic importer;
   uncertain attempts/receipts require a reviewed domain migration or continued
   old-engine ownership.
5. **Validate counts and outcomes.** Reconcile every inventory ID with its owner
   and expected final result. Keep legacy result lookup and task routes for the
   promised retention/redelivery window. Shadow only read-only decisions; never
   execute the side effect twice to compare outputs.
6. **Roll back routing, not history.** Stop new Due admission for the cohort if
   needed, but let Due finish its admitted work or transfer it with the same
   ownership protocol. Replaying Due-owned work through the old task is unsafe.
   Retain schema, receipts and recovery until all obligations are accounted for.
7. **Remove old machinery last.** Retire task/reaper/scheduler paths only when they
   own no outstanding work, no unresolved evidence and no required lookup IDs.

## Acceptance histories for each migrated operation

Bind [Due's generated application contracts](conformance.md) to the real new
producer and completion path. Add source-specific compatibility checks for the
contracts retained above. Require these observations before production cutover:

| History | Observable outcome |
| --- | --- |
| Product transaction rolls back, including nested savepoint | Neither an executable obligation nor an external effect leaks |
| Commit succeeds and notification is lost | Recovery completes the actual product result |
| Duplicate delivery or worker dies | Correct replay/evidence policy; stale local completion cannot publish |
| Provider applies but reply/local settlement is lost | Stable provider identity; no blind resend for evidence-required work |
| New desired input arrives while old request is in flight | Latest product truth and declared external repair policy hold |
| Same inputs submitted twice | The explicitly chosen occurrence/coalescing semantics hold |
| Retry budget is spent | Visible stopped outcome, with authorized rearm distinct from repeated admission |
| One identity poisons recovery | Other work progresses and the failed identity remains visible |
| Mixed old/new routing and rollback | Every accepted occurrence has one recovery owner; no orphaned result IDs |

Measure end-to-end completion latency and operating load if those motivate the
migration. Moving the side effect to another engine is not itself a performance
result. Record any intentionally changed behavior beside its passing history.
