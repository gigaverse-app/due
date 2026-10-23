# Django Tasks backend validation

The boundary is Django enqueue -> PostgreSQL TaskWork -> Due claim/recovery ->
Celery worker -> fenced TaskResult. Production code owns every state transition.
Tests replace transport only where they deliberately lose or inspect a delivery.

- [x] Standard sync and coroutine tasks: enqueue, distinct IDs, worker execution,
  normalized result and refresh through Django's public API.
- [x] Context/async surfaces: real TaskContext, actual execution attempts,
  aenqueue/aget_result/arefresh.
- [x] Transaction and savepoint rollback: durable admission and notification
  share the caller's transaction; provisional IDs disappear on rollback.
- [x] Recovery: deliberately drop notifications, recover by the production
  capability, honor persisted queue and earliest start.
- [x] Failure history: retry, exhaustion, invalid return value, traceback and
  exception-class lookup through TaskResult.
- [x] Fencing: expire a started claim, complete its successor, reject the old
  worker's result write and duplicate delivery.
- [x] Real process test: Redis, prefork Celery, PostgreSQL, a filesystem effect,
  deliberate process exit after effect, recovered result and stale-message rejection.
- [x] Validation: unsupported priority/queue/naive delay, invalid JSON and
  cross-task/cross-backend result lookup fail without corrupting admission.
- [x] Signals: committed admission/start/finish snapshots with no transaction
  held during observers.
- [x] Existing library suite: 1,031 passed with fixture contract checks.
- [x] Native coexistence, retention, configuration, decorated imports,
  database routing and publication-failure controls: 29 adapter tests pass.
- [x] Django 6.0.8 and 6.1.2 adapter tests; the installed wheel also passes all
  29 adapter tests with Django 6.0.9 and published pytest-obligation 0.15.0.
- [x] Application suite: 980 passed, two expected failures. Installed-wheel
  runtime imports/migrations, strict docs build and type checks pass.
