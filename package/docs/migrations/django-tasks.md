# Coming from Django Tasks and the django-tasks backport

Due now implements Django's built-in Tasks API through the optional
`due.django.tasks` app. Start with the [backend setup guide](../django-tasks.md)
to keep `@task`, `.enqueue()` and refreshable `TaskResult` objects. The guide
below also describes migration into a native domain capability.

The backend supports replay-safe sync and coroutine task bodies, context,
delays, named queues and retained results. The older `django_tasks` backport
is unsupported. Nonzero priority is rejected explicitly.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Updated **2026-10-24** for the implemented backend; earlier upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
The native examples below remain an alternative to the standard task backend.

## Why consider Due?

For tasks that represent product obligations, Due can provide shared recovery,
fenced product completion and explicit provider uncertainty instead of leaving
those mechanisms inside each task. Atomic admission can close a gap when the
current backend is reached only after the product transaction commits. It adds
no new atomicity if your database backend already inserts work transactionally.

Keep your current backend when ordinary task execution and TaskResult are enough.
Due's richer semantics cost concrete work models, migrations and Celery/broker
operation. Its current Python/Django requirements also apply when the source API
is the backport. A supported frontend on an older Django version does not make
Due run on that version.

## Prepare the Due side

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


## The interfaces you will work with

| Familiar primitive | Its strength | Due counterpart and semantic change |
| --- | --- | --- |
| `Task` / `@task`, `.enqueue()` | A standard task frontend independent of backend choice | `due.django.tasks.backend.DueBackend`, or a native domain capability |
| `TaskResult`, `.refresh()`, `.return_value` | Typed task identity, status and result lookup | `PublicationWork` tracks obligation state; `Document` owns the business result |
| `.using(run_after=..., priority=...)` | Per-enqueue options where the backend supports them | `not_before` for new work; priority and arbitrary routing are not equivalent supported options |

## Before: the task and its caller

The comparison publishes document 42's text to an immutable object and records
its key in `Document.published_key`. The source example's application helper
`write_revision(text, publication_id)` writes the same bytes to a stable key on
retries and returns that key. `publication_id` is allocated once per request,
not on each retry. This helper is application code, not a library API; the Due
[shared implementation](../migrations.md#shared-native-implementation) shows the file-write body.

These are focused integration excerpts: put definitions in importable app
modules, configure the source worker/backend, and provision the `Document` row.
The deliberately simple source task has no application-specific stale-write
fence. If yours already has one, preserve its contract and compare what code Due
would replace; the source library is not inherently unable to implement it.

```python
from django.tasks import task
from .models import Document
from .storage import write_revision


@task
def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_revision(text, publication_id)
    Document.objects.filter(pk=document_id).update(published_key=key)
    return key


task_result = publish.enqueue(42, "Hello", "request-123")
# Later, once the backend reports SUCCESSFUL:
task_result.refresh()
key = task_result.return_value
```

The spelling change is from `publish.enqueue(...)` to an application producer
that commits the owner and work together. A `TaskResult` cannot be replaced with
a work row without updating status/result consumers. `task_result.refresh()`
and `.return_value` have no native Due equivalents returning the same contract.

The framework delegates execution guarantees to its backend. Evaluate the
backend you actually run: replacing an immediate development backend differs
from replacing transactional database admission. The concrete new boundary is
`finalize_success`, where the product update gains Due's revision/token check.

## After: model the obligation, split execution from completion

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


## Delayed delivery versus a movable deadline

With a timezone-aware `deadline`, the familiar delayed call is:

```python
publish.using(run_after=deadline).enqueue(42, "Hello", "request-123")
```

For a fixed initial delay, change the final line **inside the Due producer's
existing `atomic()` block** to:

```python
return pending.request_work(
    PublicationWork(text=text, universe="example"),
    not_before=deadline,
)
```

That sets when new work becomes eligible; delivery can happen later. Sending
unchanged inputs again is not a command to move an existing deadline. When the
product concept is “do this at a deadline that may change,” declare a separate
`ScheduledWork` model and `ScheduledAction` with `is_still_needed`,
`perform_effect` and `on_completed` hooks. Its policy requires `retention=None`;
explicit `input_fields` must include `run_at` and `cancelled`.
[Declaration example](../../tests/declarations/test_simple_callbacks.py).

For that registered capability, the native calls have meaningful result types:

```python
from due.django.capabilities.scheduled import CancellationResult, ScheduleResult

# deadline_action and DeadlineWork are your declared capability and work model.
status: ScheduleResult = deadline_action.schedule_at(
    42,
    DeadlineWork(universe="example"),
    at=deadline,
)
cancellation: CancellationResult = deadline_action.cancel_pending(42)
```

Handle `ALREADY_STARTED` from either operation: Due cannot promise prevention
once an attempt may have escaped. `UNCHANGED` from scheduling and
`NOT_SCHEDULED` from cancellation are distinct outcomes too. The useful gain is
an explicit, persisted deadline/cancellation contract; this does not reproduce
the source library's general revoke, cron or priority API.

## Migrate with the current Due API

Determine both the import (`django.tasks` versus `django_tasks`) and the installed
backend. Current backport versions separate database and RQ workers into
`django-tasks-db` and `django-tasks-rq`; older deployments may have bundled them.
Their storage/migration history cannot be replaced by changing one setting.
[Backport](https://github.com/RealOrangeOne/django-tasks),
[DB backend](https://github.com/RealOrangeOne/django-tasks-db).

**Native migration path:** replace selected `.enqueue()` producers with explicit
Due admission and migrate their task bodies as described above. Keep other
backend aliases intact. The source function's return value, error list, attempts,
worker IDs and refreshable result are consumer contracts to preserve or explicitly
change. The example Due publication returns a work row, so it is not a drop-in
replacement for a caller expecting `.return_value`.

**Standard API path:** configure [DueBackend](../django-tasks.md), declare
replay-safe functions, run its migrations, and include its registration beside
your native Due capabilities. This preserves the task/result API for the
supported features.

When migrating from an enqueue performed in `transaction.on_commit`, move durable
admission **inside** the product transaction. Leaving it in the callback still
permits the process to die after committing the product but before admitting
work. Conversely, do not claim to fix this gap for an existing database backend
that already inserts atomically on the same connection. For async request code,
run the product mutation and Due admission in one synchronous transactional
callable; an async enqueue wrapper alone does not join unrelated transactions.

## Cutover and rollback

Identify the configured backend, not just the decorator import. Deploy Due and
route only selected new operations to it; leave other tasks on their existing
backend aliases. Drain old work with the old worker and retain result lookup for
IDs clients already hold. A result ID needs its backend identity as well as its
string value during coexistence.

Do not reinterpret existing backend rows as Due work. Imported occurrences need
an explicit unique mapping, preserved arguments, provider keys and outcome
classification; unknown external calls stay with their evidence owner until a
reviewed transfer. Rollback changes new admissions while Due finishes admitted
work. Keep its registrations and recovery active for that cohort.

For backend-specific operations, the dedicated
[django-tasks-db](django-tasks-db.md) and
[django-tasks-rq](django-tasks-rq.md) guides include the corresponding cutover
inventory; the native migration above does not depend on an adapter shipping.

## Could you keep the standard task interface?

Yes. The [implemented backend](../django-tasks.md) uses one concrete task work
table and the shared Due runtime. Each enqueue creates a new invocation;
identical inputs do not coalesce. Arguments and return values round-trip through
JSON. Admission joins the default Django transaction; notification follows commit.

Results expose actual worker starts, first/last start times, completion, errors,
and process identities. Retryable work projects to READY, claimed work to RUNNING,
success to SUCCESSFUL, and stopped failures to FAILED. Result storage and failure
history share Due's settlement fence. UNKNOWN is excluded by the replay-safe
execution contract; use native evidence-aware capabilities for uncertain effects.

Move selected tasks to native capabilities when you need desired-state
coalescing, fenced product updates, independent compensation, movable deadlines,
or durable evidence about non-repeatable effects. Both modes can share a
registration manifest. Migrating an existing invocation to a different capability
is not automatic: drain old occurrences and keep their handlers and result lookup
available while directing new admissions to the richer model.

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

For a backend implementation, additionally verify two equal enqueues create two
invocations, result refresh/error behavior matches the pinned Django version,
unsupported options fail before admission, and rollback removes provisional
result IDs. Exercise a native advanced capability through the integration: if
UNKNOWN or fenced business completion is inaccessible, the adapter has become a
limiting endpoint rather than a migration path.
