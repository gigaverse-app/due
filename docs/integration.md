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

## Tenant and replica hooks

The persisted `universe` field is the tenant key. The default implementation
restores a context-local tenant available through `current_tenant()`.
Applications that already own tenant context and replica pinning install their
existing implementations once during startup:

```python
from due.django.integration import ApplicationHooks, configure

configure(
    ApplicationHooks(
        tenant_scope=my_application_tenant_scope,
        pin_after_write=my_application_pin_after_write,
    )
)
```

Override `work_beneficiary_user_id()` when settlement must pin a beneficiary's
subsequent reads. Hooks preserve the existing backend boundary without Due
importing its code. Registration/configuration is process-wide; configure before
serving requests, not per request.

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
- Configure existing tenant/routing hooks and update monitoring to `due_*` metrics.
- Run the existing application lifecycle tests and generated contracts through
  the new imports before removing the original implementation.

The retained class/method names reduce source changes. Migration and queued-message
compatibility still require deliberate handling; this is not a zero-migration swap.
Native-table users remain on their existing implementation and are outside Due's
first release.
