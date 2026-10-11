# Coming to Due from another library

Choose the library you use today. Each guide puts the migration decision,
source/native code, comparable types and primitives, setup, semantic changes,
concrete gains, cutover, rollback and
compatibility-layer assessment in **one document**. You do not need to assemble
a plan from separate comparison and adapter reports.

| Coming from | What the guide helps you preserve |
| --- | --- |
| [Procrastinate](migrations/procrastinate.md) | Transactional deferral, execution/queueing locks and stalled jobs |
| [DBOS](migrations/dbos.md) | Bounded operations, checkpointed workflows and durable waits |
| [RQ](migrations/rq.md) | Job identities, results, dependencies and Redis cutover |
| [Django Tasks / django-tasks](migrations/django-tasks.md) | Native migration today and the proposed standard backend |
| [django-tasks-db](migrations/django-tasks-db.md) | ORM task rows, db_worker and result retention |
| [django-tasks-rq](migrations/django-tasks-rq.md) | TaskResult plus RQ delivery and backend-aware lookup |
| [django-rq](migrations/django-rq.md) | Django producer paths, queue settings and admin continuity |
| [Celery](migrations/celery.md) | Keep the workers; replace custom obligation recovery |
| [Chancy](migrations/chancy.md) | Transactional push, uniqueness, plugins and executors |
| [PgQueuer](migrations/pgqueuer.md) | Connection-bound admission, async handlers and serialized dispatch |
| [Dramatiq](migrations/dramatiq.md) | Actors, middleware, retries and successor handoffs |
| [Huey](migrations/huey.md) | Task calls, results, locks, revoke and scheduling |
| [arq](migrations/arq.md) | Async context, job IDs, expiry and cancellation |
| [Taskiq](migrations/taskiq.md) | Broker plugins, dependency injection, labels and results |
| [Django Q2](migrations/django-q2.md) | Broker choices, q_options, hooks and missed schedules |
| [Temporal](migrations/temporal.md) | Activity boundaries, workflow ownership and cancellation |
| [PGMQ Python clients](migrations/pgmq.md) | Visibility, acknowledgement, archive and message transfer |

## Read the decision before changing the code

Due is useful when an application must remember a business obligation beyond
one execution attempt. The guides identify concrete benefits—atomic product/work
admission, fenced local completion, explicit provider uncertainty and reusable
recovery—without assuming these are absent from your current system.

They also explain when to stay. PostgreSQL queues already provide durable,
transactional enqueue; workflow engines provide execution history and orchestration
that Due does not replace. For the broader picture, read
[Due and the Python background-work ecosystem](alternatives.md).

The current package is alpha, requires Python 3.14+, Django >=6.0.8,<7,
PostgreSQL and Celery with a broker and recurring recovery. No compatibility
adapter described in these guides is implemented. Each guide distinguishes
migration through today's native API from possible future integration.

The native target is grounded in the existing [runnable document example](../examples/documents/).
Its source remains the canonical implementation; the guides explain how each
source library's semantics map to it and where the domain needs a different model.
Each guide shows the source task and caller, the concrete Due models, the
effect/completion split, and the atomic producer. Source examples assume a
configured client/backend and importable application modules. The native excerpts
come from the runnable example; the side-by-side comparisons are not claims of
executed cross-library parity.

Reviewed on **2026-10-11** against Due `65e0bcf`. The
[source register](alternatives.md#source-register) records the inspected upstream
snapshots. These guides make no comparative performance or production-readiness
claim; validate the chosen path against your installed versions and real workload.
