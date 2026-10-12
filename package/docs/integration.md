# Django and Celery integration

Use one writable PostgreSQL database for the product row, its work row, Due's
attempt evidence and recovery deferrals. This release uses Django's default
transaction connection; cross-database atomic admission is not supported.

1. Add `due.django` to `INSTALLED_APPS` and migrate.
2. Define your concrete work models, owner relation, captured input fields and
   `durable_work_schema(...)`. Generate and apply your application migrations.
3. Implement a capability with an explicit `DurableWorkPolicy` and register it
   in every web, worker, Beat, management-command and test process that uses it.
4. Call `configure_durable_work` once to install the manifest, task imports and
   recovery schedules. Configure Celery from Django settings or pass its app explicitly.
5. Run workers for the registered queues and a recurring scheduler. Alert on
   outstanding work and stale recovery heartbeats.

The complete registration pattern is in `examples/apps.py`:

```python
from due.django.runtime.registry import configure_durable_work
from myapp.registry import REGISTRATIONS

# In AppConfig.ready(): one manifest owns routes, task imports and Beat schedules.
configure_durable_work(REGISTRATIONS)
```

This validates and installs registrations, adds Due's task module to
`CELERY_IMPORTS` and merges recovery schedules with existing Django settings.
Unrelated imports and schedules are preserved. A generated schedule name owned
by another task or route is an error, checked before configuration changes.
Repeating the same setup is safe; an existing schedule for the same Due route is
updated from the manifest.

If the Celery app already loaded its configuration or does not read Django's
`CELERY_*` settings, pass that app during startup:

```python
configure_durable_work(REGISTRATIONS, celery_app=my_celery_app)
```

This also updates the explicit app's task imports and recovery schedule. It does
not change broker URLs, serializers or unrelated application settings. Configure
before starting workers or serving requests; this is not a running-worker reload.
No import-time autodiscovery or application dependency is added to Due.

Run `python -m django due_check` in the same environment as each process. This
command verifies configuration, not remote broker connectivity or worker
availability. The lower-level registry functions remain available for applications
that own their setup explicitly.

Resolve registrations before executing work. Missing registration is a startup
or deployment defect, not a retryable provider failure. Stable names are part of
the message contract. With `django-celery-beat`, the optional
`remove_stale_recovery_schedules(routes)` removes only unowned **Due** schedules.
It does not remove another application's task names.

## One composition root, every process

Keep application wiring in one explicit startup function. Django loads your
models before capabilities can safely be registered; invoke that function after
Django setup and before accepting requests or consuming tasks. A configured web
process does not configure a separately started worker or Beat process.

```text
                    application startup / composition root
                                      |
                 +--------------------+---------------------+
                 |                    |                     |
          ApplicationHooks     capability registry    recovery schedules
          tenant + read pin    stable route names     same cadence/queues
          fatal error types    worker time limits     as the worker registry
                 |                    |                     |
                 +--------------------+---------------------+
                                      |
                    web / worker / Beat / commands
                    each process installs its own copy
```

For an existing database, read the migration section below **before** running
Due's initial migration. The quick-start steps above describe a fresh install.

## Tenant, replica and fatal-error hooks

The persisted `universe` field is the tenant key. The default implementation
restores a context-local tenant available through `current_tenant()`.
Applications that already own tenant context and replica pinning install their
existing implementations once during startup. In this sketch the three names
are application-owned: a tenant context manager, a read-pin function, and the
exception class raised when an application dependency has not been installed:

```python
from due.django.integration import ApplicationHooks, configure

configure(
    ApplicationHooks(
        tenant_scope=my_application_tenant_scope,
        pin_after_write=my_application_pin_after_write,
        abort_recovery_on=(ApplicationNotInstalled,),
    )
)
```

Override `work_beneficiary_user_id()` when settlement must pin a beneficiary's
subsequent reads. Hooks preserve the existing backend boundary without Due
importing its code. Registration/configuration is process-wide; configure before
serving requests, not per request. The tenant context manager must restore its
previous value even when work fails. Due also maintains its own `current_tenant()`
context around that application scope.

### A bad row is different from a broken process

A malformed identity must not prevent other identities from recovering. A missing
application service invalidates the whole tick. Register its **exception type**
with `abort_recovery_on`; matching a class name or inheriting from Due's private
installation error is unnecessary.

```mermaid
flowchart TD
    A[Recovery step raises] --> B{Tick cannot safely continue?}
    B -->|Configured application error| C[Abort and propagate]
    B -->|Soft time limit or broken transaction| C
    B -->|Ordinary identity failure| D[Record failure and defer identity]
    D --> E[Continue other identities and phases]
    E --> F[Report failed tick]
    C --> G[Report aborted tick]
    F --> H[Do not advance clean heartbeat]
    G --> H
```

The classifier follows exception causes and contexts, including a wrapper raised
by a lock helper. Existing Due wiring errors and Celery soft limits still abort,
even when the application declares no extra types. Per-call `propagate` types on
`recovery_tick` continue to apply too.

Choose narrow configuration-error classes. Registering `Exception` would abort on
every ordinary row failure and remove poison isolation. This hook classifies
**recovery** errors; it does not change provider replay permission or the
execution capability's error policy. No error clears UNKNOWN evidence.

The executable [recovery isolation test](../tests/capabilities/test_operation_recovery_isolation.py)
runs both receipt settlement and negative-evidence recovery. It checks that a
fatal application error leaves the next identity untouched and creates no row
backoff, while an ordinary error lets that identity progress. The
[reporting test](../tests/runtime/test_recovery_pass.py) also checks aborted
outcomes and the absence of a clean heartbeat.

## Adopting the extraction in an existing backend

This repository does not migrate the originating backend automatically. Preserve
its product/work tables, captured fields, migration history, registered task names
and unresolved provider evidence during a separate consumer cutover.

- Change library imports to `due.django`, with pure contracts in `due.errors`,
  `due.observations` and `due.retry_delays`.
- Retain concrete model/table identities. Historical migrations must keep their
  frozen schema definitions; never rewrite migration history to import live code.
- Due's concrete tables are `due_effectattempt` and `due_recoverydeferral`.
  Move existing evidence with an explicit, reviewed data/state migration and
  repoint receipt foreign keys. Do not initialize empty evidence tables beside
  existing uncertain operations or copy only the work rows.
- Drain or route old task names before removing their worker entry points. Only
  after the data/route cutover should all processes use Due's registrations.
- Configure existing tenant/routing/fatal-error hooks and preserve or explicitly migrate the configured metric namespace.
- Run the existing application lifecycle tests and generated contracts through
  the new imports before removing the original implementation.

The retained class/method names reduce source changes. Migration and queued-message
compatibility still require deliberate handling; this is not a zero-migration swap.
All 17 registrations in the originating backend are work tables; there are no
direct production adopters of its native lifecycle APIs. Two production modules
use a counter helper located in the old `native` package; that arithmetic import
can import the existing `due.django.runtime.counters` arithmetic helper. Older application workflows
and their tests do not need to adopt a native API to survive the work-table port.

Use the [backend audit](backend-adoption.md) for application migration gates and
the [conformance guide](conformance.md) to bind the existing domain suites.

## Migrating an existing application

Configure these settings before Django imports Due:

| Setting | Default | Purpose |
| --- | --- | --- |
| `DUE_EFFECT_ATTEMPT_TABLE` | `due_effectattempt` | Existing evidence table name |
| `DUE_RECOVERY_DEFERRAL_TABLE` | `due_recoverydeferral` | Existing recovery-deferral table name |
| `DUE_TASK_NAMESPACE` | `due.django.runtime.tasks` | Stable names understood by existing workers |
| `DUE_METRIC_NAMESPACE` | `due` | Stable Prometheus family prefix |

Changing a table setting does not move data or rewrite migrations. An adopter
with existing tables must supply its reviewed Django migration module, preserve
physical constraints and foreign keys, and prove forward/backward migration with
populated rows. Keep that application migration history current when adopting
future Due schema changes. New applications should use the default tables and
shipped migrations.

Keep task names stable during rolling deployments: changing a Python import is
separate from changing the Celery message name. Older historical aliases can be
bound by the application's Celery registry to the same task objects. Pass exact
`retired_task_names=(...)` to `remove_stale_recovery_schedules` when reconciling
persisted Beat entries under previous names; another application's recovery task
must never be removed by a suffix match.

`due.django.runtime.metrics.metric_schemas()` exposes read-only label schemas and
enum vocabularies from the declarations that construct the metrics. Applications
can check alerts against this catalog without duplicating metric definitions.

### Short database transitions and application completion

Use `DatabaseEffect` for short PostgreSQL-only transitions whose business writes
and continuation must commit together. Its `transition(work)` callback receives a
work row with the canonical owner already locked and loaded; return `ContinueAfter`
to release the worker. Existing eligibility, atomic admission, retry policy,
exhaustion mirrors and dependent wakeups compose with it. Provider I/O continues
to use `ReplaySafeEffect`, `Projection`, `ScheduledAction` or an evidence-aware
operation. Database atomicity cannot protect a remote side effect.

For conformance, bind `EffectExamples.delivery_replays` using
`due.django.conformance.database_replay.database_replay`. It counts production
delivery entries separately from callback entries and requires a declared product
outcome. This mode does not claim profile H's raw external-effect idempotency.
Use `EffectExamples.completions` with
`due.django.conformance.completion.completion` to require the final outcome under
the real registered cadence, optionally losing every transient hint. The books
example checks searchable publication across both stages; Widget Store checks
paid and refunded outcomes with full one-second waits. These are additional
proofs, not replacements for crash, concurrency or external-effect contracts.

The originating backend still has its vendored engine. These APIs become usable
there through the package migration described above, preserving its existing
tables and message names. Copying a second implementation into that engine would
create drift. Its provider-backed capabilities should retain their current modes;
the reusable terminal-outcome bindings can strengthen their application proofs
without moving provider I/O under database locks.

The standalone storefront uses Django's supported psycopg connection pool
(`psycopg-pool` in the demo environment) to avoid reconnecting after each task and
HTTP request. This is deployment configuration, not an engine requirement. Pools
are per process and have bounded size; callers still need a connection budget for
their worker count. See [Django's connection-pool documentation](https://docs.djangoproject.com/en/6.0/ref/databases/#connection-pool).

### Worker connection lifetime

Due's Celery tasks use Django's public connection cleanup at entry and return.
The default zero connection age returns pooled sockets on each delivery, while
pool checkout health checks can replace sessions lost during idle time. Configure
`CONN_HEALTH_CHECKS=True` for pooled/persistent connections. Eager execution
preserves its caller's transaction. No task body is automatically retried by this
cleanup: unresolved work and uncertain effects retain their existing recovery
and evidence rules.

The standalone Widget Store demonstrates bounded pool reuse with these task
boundaries. Embedded Beat is a single-worker demo configuration; production
fleets should retain one dedicated scheduler.

### Beat wakeups and serial worker pools

Manifest setup selects `due.django.runtime.beat:RecoveryScheduler` when
Celery's stock persistent scheduler is configured. It preserves Celery's
intentional 10 ms early check and existing schedule file, then sleeps the final
positive remainder instead of spinning. Custom scheduler classes remain owned
by the application. The recovery cadence and Due's transient deadline hints do
not change.

For a Celery **prefork child that uses one database connection at a time**, size
its Django pool with `min_size=1, max_size=1`, retaining
`CONN_HEALTH_CHECKS=True` and task connection boundaries. Each child owns a
separate pool. This avoids unnecessary spare sockets and repeated stale-socket
checks after session loss; psycopg's outage backoff remains intact. Threaded
web servers and workers using concurrent database access need their own larger
pool. The widget supervisor demonstrates separate worker and web settings.
