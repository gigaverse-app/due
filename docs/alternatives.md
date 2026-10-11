# Due and the Python background-work ecosystem

These libraries are **alternatives and potential integration partners**. They
solve overlapping problems with different tradeoffs, and some can remain useful
alongside Due. The right migration starts with an application need, not a claim
that one project should replace every other project.

For a provider-independent explanation of the decision, read
[When Due is the right tool](why-due.md): its strengths, application costs, and
how to check the failure assumptions behind a proposed migration.

Due's strongest current fit is a Django/PostgreSQL application that already uses
Celery and maintains custom obligation recovery, revision or provider-evidence
logic. Moving a satisfactory PostgreSQL queue only to obtain durable admission
is usually not worthwhile; that capability is already available with fewer
infrastructure components.

This source/documentation assessment was reviewed on **2026-10-11**, against Due
[`65e0bcf`](https://github.com/gigaverse-app/due/tree/65e0bcfe426c9e663aec4ae5c71398965dde3269).
Recommendations are engineering judgments, not comparative benchmarks. No
compatibility adapter or Django Tasks backend ships at this revision.

## Find your migration guide

Each linked guide owns the benefits, migration instructions, caveats and adapter
assessment for that library. Framework integrations have their own guides because
the frontend contract and the execution backend can impose different obligations.

| Library | How it relates to Due | Start here |
| --- | --- | --- |
| Procrastinate | PostgreSQL task queue with transactional deferral and locks | [Coming from Procrastinate](migrations/procrastinate.md) |
| Chancy | PostgreSQL queue, executors and plugin ecosystem | [Coming from Chancy](migrations/chancy.md) |
| PgQueuer | PostgreSQL queue with async handlers and connection-bound enqueue | [Coming from PgQueuer](migrations/pgqueuer.md) |
| Django Tasks / django-tasks | Standard task API and an integration opportunity | [Coming from Django Tasks](migrations/django-tasks.md) |
| django-tasks-db | ORM execution backend for that API | [Coming from django-tasks-db](migrations/django-tasks-db.md) |
| django-tasks-rq | RQ execution behind the standard task API | [Coming from django-tasks-rq](migrations/django-tasks-rq.md) |
| RQ | Callable jobs and results backed by Redis | [Coming from RQ](migrations/rq.md) |
| django-rq | Django settings, producers and administration for RQ | [Coming from django-rq](migrations/django-rq.md) |
| Celery | Existing task API and Due's execution transport | [Coming from Celery](migrations/celery.md) |
| Dramatiq | Actors, broker delivery and middleware | [Coming from Dramatiq](migrations/dramatiq.md) |
| Huey | Task execution, results and scheduling | [Coming from Huey](migrations/huey.md) |
| arq | Async Redis jobs and worker context | [Coming from arq](migrations/arq.md) |
| Taskiq | Async task API with broker and result plugins | [Coming from Taskiq](migrations/taskiq.md) |
| Django Q2 | Django queue, scheduler, workers and results | [Coming from Django Q2](migrations/django-q2.md) |
| DBOS | Durable workflows, checkpoints and transaction integration | [Coming from DBOS](migrations/dbos.md) |
| Temporal | Durable workflow platform with a Python SDK | [Coming from Temporal](migrations/temporal.md) |
| PGMQ Python clients | Lower-level PostgreSQL messaging | [Coming from PGMQ](migrations/pgmq.md) |

## What Due can add

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

## Which integration work is worth pursuing?

Begin with native adoption in existing Django/Celery applications: infrastructure
already fits, and one recovered product failure can establish value. A constrained
Django Tasks backend is the most natural standard-API experiment; its full proposed
contract lives in the [Django Tasks guide](migrations/django-tasks.md#could-you-keep-the-standard-task-interface).
Other producer facades should follow demonstrated application demand.

Familiar syntax, preserved semantics and unchanged operations are separate
promises. Keeping `.enqueue()` does not preserve the old result store, worker,
CLI or dashboard. A thin adapter delegates to Due's existing lifecycle; an
adapter with its own retry engine, lock service or workflow log has stopped being
thin.

An unchanged task body can benefit from durable admission and recovery, but its
internal product writes do not inherit Due's finalization fence. Provider keys,
evidence, projections, movable deadlines and compensation require explicit
application declarations. The migration remains useful only if those native
capabilities are accessible rather than hidden behind a permanently generic
function interface.

Before implementing an adapter, prove a real source application's required
result, transaction, ordering and cancellation behavior. Reject unsupported
options before admission. Stop if compatibility requires a second execution
owner or hides UNKNOWN as an ordinary retryable failure. Workflow systems are
usually better integration partners at a bounded operation boundary than targets
for interface emulation.

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
| django-rq | [official repository](https://github.com/rq/django-rq), reviewed 2026-10-11 | Django producer helpers, settings, workers and admin integration |
| django-tasks-db | [official repository](https://github.com/RealOrangeOne/django-tasks-db), reviewed 2026-10-11 | Separate ORM backend, worker and result pruning |
| django-tasks-rq | [official repository](https://github.com/RealOrangeOne/django-tasks-rq), reviewed 2026-10-11 | Separate RQ execution backend for Django Tasks |
