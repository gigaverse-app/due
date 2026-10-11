# Due and other Python background-work libraries

**Due's strongest current fit is a Django/PostgreSQL application that already
uses Celery and has implemented its own recovery, revision, or provider-evidence
protocols.** Migrating a satisfactory queue solely to obtain durable enqueueing
is usually not worthwhile. PostgreSQL queues already offer that capability,
with fewer infrastructure components than Due.

This is a source/documentation audit dated **2026-10-11**, against Due
[`65e0bcf`](https://github.com/gigaverse-app/due/tree/65e0bcfe426c9e663aec4ae5c71398965dde3269).
Recommendations below are engineering judgments, not comparative benchmarks or
claims that competitors cannot implement these patterns. See the
[source register](#source-register) for inspected versions and evidence.

Read [the migration guide](migrations.md) for implementation and cutover, and
[the adapter assessment](compatibility.md) before promising API compatibility.
No competitor shim or Django Tasks backend ships in this revision of Due.

## Who competes directly?

| Library / family | Relationship to Due | Reason to consider Due | Reason to stay |
| --- | --- | --- | --- |
| **Procrastinate** | Closest PostgreSQL Python task-queue competitor | Replace application-owned stale-result fencing, uncertain-operation tracking and settlement recovery | Transactional enqueue, execution/queueing locks, async workers and PostgreSQL-only operation already meet the requirement |
| **Chancy** | Close PostgreSQL task-queue competitor | Make product completion and evidence explicit for complex effects | Transactional enqueue, multiple executors, rate limits, workflows and dashboard without a separate broker |
| **PgQueuer** | Close PostgreSQL task-queue competitor | Adopt Due's work-table lifecycle where queue execution leaves application recovery code | Async handlers, transactional enqueue, scheduling and PostgreSQL-only operation are a better fit |
| **django-tasks-db and similar database backends** | Direct competitor for Django background execution | Use Due's recovery/settlement policies for business obligations | Ordinary queued functions are enough; avoid Celery/broker and bespoke work models |
| **Django 6 `django.tasks` / `django-tasks` backport** | API and integration opportunity, not an execution-engine competitor | Potentially retain the standard producer interface with a Due backend | No such Due backend exists today; evaluate the actual installed backend |
| **Celery** | Incumbent application API **and Due's required transport** | Replace hand-built outbox/reaper/retry/settlement plumbing selectively | Keep Celery Canvas, result backends, routing and ordinary jobs that need no richer lifecycle |
| **RQ / django-rq / django-tasks-rq** | Direct substitute for simple queued Python functions | Atomic product/obligation admission and explicit recovery in a Django/PostgreSQL application | Simpler callable/job/results model, existing Redis operations and dependencies |
| **Dramatiq, Huey, arq, Taskiq, Django Q2** | Other general-purpose task-queue alternatives | Selected effects need more than whole-function retry | Framework/runtime fit, existing middleware/results/scheduler integrations, smaller migration |
| **DBOS** | Overlaps in durable background work; broader workflow competitor | A particular Django lifecycle benefits from explicit evidence and revision handling | Checkpointed workflows, transaction integration, durable waits and workflow management |
| **Temporal Python SDK** | Adjacent durable-workflow platform | Extract a bounded Django-owned effect when ownership is clear | Long-running, cross-service workflow history, signals, timers and orchestration |
| **PGMQ Python clients** | Lower-level PostgreSQL messaging substrate | Replace application-built consumer completion/recovery protocol | Language-neutral queue operations and existing consumer infrastructure |

Prefect, Dagster and Airflow are primarily orchestration candidates for data
pipelines, rather than the first migration targets for product-side effects.
This audit does not assess their API compatibility. Other Django Tasks backends
need individual assessment; sharing the frontend API says little about durability.

## What Due actually adds

Due persists work alongside a product update, separates external execution from
fenced local settlement, and retains uncertain external attempts for evidence.
Its [capabilities](capabilities.md) cover replay-safe effects, projections,
movable deadlines, non-repeatable operations, observation and compensation.
Applications can bind the [conformance suite](conformance.md) to their real
producer, provider and completion paths.

| Failure or requirement | What can be a meaningful gain | What does not follow |
| --- | --- | --- |
| Database commit followed by lost publication | Same-transaction admission plus periodic discovery of outstanding work | Not a unique advantage over transactional PostgreSQL enqueueing or a correct outbox |
| Worker dies after the provider acts | Replay-safe repetition **or** persisted UNKNOWN with correlated evidence, chosen explicitly | Due cannot deduce remote nonapplication from a timeout |
| Old worker finishes after new desired inputs | Captured revision/token fences local settlement | An old HTTP request can still affect the provider; immutable keys, provider ordering or repair remain necessary |
| Provider result exists, local completion fails | Evidence-backed operation can repair local state without resending | Arbitrary replay-safe callback return values are not automatically durable receipts |
| Deadline moves or is cancelled | Persisted schedule and eligibility recheck through `ScheduledAction` | Cancellation cannot unsend an admitted remote operation |
| Task succeeds but follow-up is lost | Admit the next obligation in the settlement transaction | Due is not automatically a workflow graph or checkpointed Python interpreter |
| Custom recovery code is hard to verify | Generated histories bind actual application seams | Library tests do not certify a provider or an unbound application |

See [architecture](architecture.md) and [verification](verification.md) for the
implemented boundaries and their existing evidence. This audit ran no comparative
fault-injection or throughput experiment; it does not claim faster execution,
lower cost, greater scale, or universally stronger durability.

## The strongest competing cases

### Procrastinate: compete on lifecycle semantics

Procrastinate has transactional deferral through an external connection, task
retries, two different lock contracts, scheduled/periodic tasks and stalled-worker
detection. Its documented stalled-job recovery uses an application periodic task.
Those are substantive existing capabilities, not missing reliability features.
[Transactional deferral](https://procrastinate.readthedocs.io/en/stable/howto/production/external_connection.html),
[execution locks](https://procrastinate.readthedocs.io/en/stable/howto/advanced/locks.html),
[queueing locks](https://procrastinate.readthedocs.io/en/stable/howto/advanced/queueing_locks.html),
[stalled jobs](https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html).

A credible migration removes application code that reconciles uncertain provider
outcomes or prevents stale product publication, while preserving its ordering and
replay policy. If the application only needs a reliable function queue, Due adds
Django/Celery requirements without an established benefit. Generic `.defer()`
compatibility would preserve too little of this library's distinctive contract.

### DBOS: do not confuse workflow recovery with queue retry

DBOS checkpoints workflow steps and reconstructs workflow execution using saved
results. Its current datasource API couples database writes and transaction
tracking. These are capabilities Due does not reproduce. External effects still
need a safe response to an interrupted step; that is distinct from the database
transaction guarantee.
[Workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial),
[steps](https://docs.dbos.dev/python/tutorials/step-tutorial),
[datasources](https://docs.dbos.dev/python/tutorials/transaction-tutorial).

Recommend Due only for a bounded effect whose application would benefit from
Due's explicit evidence/settlement model. Do not recommend replacing a working
DBOS workflow with a single retried Due callback: that discards checkpoints.
DBOS can also express observation/reconciliation as workflow logic; Due's
potential advantage is reusable domain-oriented machinery, not an exclusive
ability to represent uncertainty.

### Django Tasks: the best extension point, with unfinished work

Django defines `BaseTaskBackend`, `Task`, `TaskResult` and backend feature flags.
The standard API is a promising producer surface for Due, but result storage,
per-invocation identity and semantics must be implemented before it is a usable
integration. In current upstream `django-tasks`, database and RQ execution live
in separate packages, split out before version 0.12.0; older installations may
still use the bundled paths.
[Django API](https://docs.djangoproject.com/en/6.0/ref/tasks/),
[backport](https://github.com/RealOrangeOne/django-tasks),
[database backend](https://github.com/RealOrangeOne/django-tasks-db),
[RQ backend](https://github.com/RealOrangeOne/django-tasks-rq).

This is the most natural adapter experiment, **not a configuration change
available today**. It also cannot bring Due to older Django/Python installations
merely because the frontend has a backport.

## Recommended order of investment

1. **Native Celery/Django adoption guides first.** The infrastructure already
   matches. Pick one application lifecycle with a demonstrated failure or a
   recovery protocol that Due can replace, and measure the application result.
2. **A constrained Django Tasks backend prototype.** Use the explicit extension
   point; prove transactional admission and TaskResult fidelity before broadening
   support. Keep advanced Due declarations available outside the standard API.
3. **Procrastinate migration guide and named producer bindings.** High conceptual
   relevance, but preserve locks, identity and connection semantics explicitly.
   A broad compatibility package needs adopter demand and more evidence.
4. **RQ/Dramatiq/Huey and other enqueue facades only on demand.** Support a named
   subset for already-declared Due capabilities. Do not reproduce every job,
   result, worker and middleware API.
5. **DBOS/Temporal interoperation, not interface emulation.** Preserve workflow
   ownership; migrate only isolated, bounded operations or leave them in place.

A proposal earns further investment when the same workload sheds a concrete
recovery mechanism, survives its required failure histories, and retains all
needed results, scheduling and operational behavior. Stop if it merely renames
enqueue while requiring a second result, lock or workflow engine.

## Source register

Upstream documentation is mutable. Source snapshots below were inspected in
addition to the linked manuals; these are repository commits, **not a claim that
all head features are in the reader's installed release**. Pin the source
library and installed version again before implementing an adapter.

| Source | Inspected snapshot / reference | Evidence used |
| --- | --- | --- |
| Due | [`65e0bcf`](https://github.com/gigaverse-app/due/tree/65e0bcfe426c9e663aec4ae5c71398965dde3269) | Public API, authoring transaction, registry, model states, example and capability histories |
| Procrastinate | [`8279a61`](https://github.com/procrastinate-org/procrastinate/tree/8279a61789fdf8227d6f513ad15e4b26f1b78b49) | Connection, locking, task/deferral and stalled-job contracts |
| Django | [`52e5ba1`, stable/6.0.x](https://github.com/django/django/tree/52e5ba169694cbd70c538ce62275cd2abd7647fd/django/tasks) | Task/result data and backend methods/flags; compare installed version with live 6.0 docs |
| django-tasks | [`3624c21`](https://github.com/RealOrangeOne/django-tasks/tree/3624c21dd4b6c86b2388dee612e083e0c6efd41c) | Backend split and frontend contracts |
| DBOS | [`d3f2de7`](https://github.com/dbos-inc/dbos-transact-py/tree/d3f2de758bbef8e10ca84a6b466108de07fd3be0) | Queue and datasource implementation; current docs use 3.x-era API, older applications need their own version mapping |
| RQ | [`b72c366`](https://github.com/rq/rq/tree/b72c366e53dc945a8891caa4c3f01c4af8d039a5), [queue](https://python-rq.org/docs/), [jobs](https://python-rq.org/docs/jobs/), [retry](https://python-rq.org/docs/exceptions/) | Callable enqueue, persisted jobs/results, registries and retry behavior |
| Chancy | [`b0e3e94`](https://github.com/TkTech/chancy/tree/b0e3e94fd12a567e47568d75f9d2d1d8fd996ea1), [manual](https://tkte.ch/chancy/) | PostgreSQL queue, transactional admission, plugins/executors |
| PgQueuer | [`3064102`](https://github.com/janbjorge/pgqueuer/tree/3064102fa7a478987aefb0ff2f6e0ed6d1adaaa0) | Connection-bound admission, byte payloads, scheduling and completion tracking |
| Celery | [5.6 task guide](https://docs.celeryq.dev/en/stable/userguide/tasks.html), [Canvas](https://docs.celeryq.dev/en/stable/userguide/canvas.html) | Task retries, idempotency and acknowledgements |
| Dramatiq | [guide](https://dramatiq.io/guide.html), [best practices](https://dramatiq.io/best_practices.html) | Actors, retries and duplicate delivery |
| Huey | [guide](https://huey.readthedocs.io/en/latest/guide.html) | Decorators, results, retries and scheduling |
| arq | [0.28 documentation](https://arq-docs.helpmanual.io/) | Async jobs, context, deferral and pessimistic execution |
| Taskiq | [getting started](https://taskiq-python.github.io/guide/getting-started.html) | Async task/broker architecture; detailed plugin parity not assessed |
| Django Q2 | [1.11 documentation](https://django-q2.readthedocs.io/en/master/) | Django queue/worker, broker choices, chains, results and schedules |
| Temporal | [workflow execution](https://docs.temporal.io/workflow-execution), [Python SDK](https://docs.temporal.io/develop/python) | Durable workflow identity/history and SDK surface |
| PGMQ | [SQL functions](https://pgmq.github.io/pgmq/api/sql/functions/), [Python reads](https://pgmq.github.io/pgmq-py/1.1.1/reading_messages/) | Visibility, acknowledgement/archive and message-level API |
