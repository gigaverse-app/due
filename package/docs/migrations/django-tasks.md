# Coming from Django Tasks and the django-tasks backport

Keep the distinction between your task API and its execution backend visible.
Django Tasks defines how code submits and inspects work; the configured backend
provides execution. Due could become a backend, but it is not one today. You can
migrate selected operations now through Due's native API, or evaluate the bounded
backend design in this guide.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

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
| `Task` / `@task`, `.enqueue()` | A standard task frontend independent of backend choice | A domain capability; Due does not yet implement `BaseTaskBackend` |
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

**Available path today:** replace selected `.enqueue()` producers with explicit
Due admission and migrate their task bodies as described above. Keep other
backend aliases intact. The source function's return value, error list, attempts,
worker IDs and refreshable result are consumer contracts to preserve or explicitly
change. The example Due publication returns a work row, so it is not a drop-in
replacement for a caller expecting `.return_value`.

**Possible future path:** implement the [backend proposal below](#could-you-keep-the-standard-task-interface)
and retain `@task`/`.enqueue()` for its supported subset. No `TASKS` configuration
for Due is provided here because no such backend is implemented.

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

**The following is an unimplemented design**, included here so you can judge
whether it is a useful migration path. It is not an installation recipe.


Django's [backend contract](https://docs.djangoproject.com/en/6.0/ref/tasks/)
is the intended extension point. Preserve `Task`/`TaskResult` from Django rather
than inventing lookalikes. Check the installed minor version; the audited
[source](https://github.com/django/django/tree/52e5ba169694cbd70c538ce62275cd2abd7647fd/django/tasks)
and live docs can evolve independently.

### Admission and identity

Begin with an allowlist of replay-safe, synchronous module-level functions.
Record the function identity, normalized JSON arguments and an immutable
invocation identifier in a concrete invocation owner. Use one concrete Due work
model for that invocation family and one registered capability. Each enqueue
creates a new invocation, even when arguments match. Desired-state coalescing is
a separate native binding, not a hidden optimization.

Persist the invocation and work inside the caller's default Django transaction;
register the wake for after commit. Return an admission result without running
the function. A later rollback must remove both rows and the wake. Do not keep
only an in-memory callback until commit. A returned ID inside an uncommitted
transaction is provisional; after rollback lookup must report it missing.

These invocation/result models do not exist in Due today. They add migrations,
retention and API compatibility responsibilities even though the coordination
engine is reused. Do not create dynamic Django model classes per decorated
function at import time.

### Execution and results

Delegate claim, lease, execution, retry, recovery and finalization to Due. Store
the normalized return value in the same fenced transaction as successful
settlement. Store required task metadata where the corresponding transition is
owned; if Due lacks a public hook for required information, extend the shared
owner or exclude that feature rather than maintaining a second state machine.

The result model must account for errors, first start, last attempt, completion,
worker identities and attempt counts where the selected Django contract requires
them. Due's `attempt_count` and failure-code fields are not automatically an
accurate substitute for Django's result history. A crash between function return
and settlement can rerun a replay-safe function; this profile does not promise a
durable checkpoint of arbitrary return values.

Implement `enqueue`, `get_result`, their async surfaces, task validation and
correct Django exceptions. The standard base provides async wrappers, but that
does not imply arbitrary coroutine task execution or transaction propagation
across independently scheduled calls. Initially reject coroutine bodies,
non-default priority and unsupported options using validation/feature flags.
Reject `takes_context=True` until its attempt/result metadata is correctly
populated; later support it with Django's real `TaskContext`.

Use one supported queue initially. Due registers a work model once with a fixed
route; arbitrary per-invocation `.using(queue_name=...)` cannot simply override
that registration. Multiple queues require deliberately supported routing, not
ignored user options. Likewise, do not set `supports_priority=True` just because
a Celery broker accepts a priority argument: Due's recovery selection must
preserve the promised ordering too.

A later fixed-delay profile can map `run_after` to persisted `not_before` and
set `supports_defer` only once recovery respects it. This is distinct from
business cancellation and movable deadlines.

### Status projection

For a replay-safe invocation profile, a candidate projection is:

| Due state | Django task status | Required detail |
| --- | --- | --- |
| READY, QUEUED, RETRYABLE | READY | A retry remains unfinished; preserve attempt/error metadata |
| CLAIMED | RUNNING | Expose the actual lifecycle; don't infer liveness from an old result object |
| SUCCEEDED | SUCCESSFUL | Result was persisted with fenced completion |
| EXHAUSTED, TERMINAL_FAILED | FAILED | Preserve the stopped reason and result-access errors |
| UNKNOWN | **Not representable faithfully** | Standard API has no uncertainty state; exclude evidence-required tasks from this profile |

Do not translate UNKNOWN to FAILED and invite ordinary retries, or call it
RUNNING indefinitely while hiding the need for evidence. Evidence-required native
bindings need a separate obligation view/API that exposes uncertainty. A
Django TaskResult may describe an admission helper completing, but must then be
clearly distinguished from the underlying business operation completing.

### Native upgrade path

A future integration should allow registration-time binding from a frontend task
to an application admission function and capability, with explicit input and
result projection. **This is a design requirement, not a proposed shipped API
name.** It must expose the native work identity and supported capability to
operators and application code without requiring import from Due's private
runtime. Native functions can coexist beside standard tasks in the same app.

For existing invocation work, keep those occurrences under their old capability.
Move new admissions to the richer domain model only when identity and result
contracts are mapped. Switching the implementation of a task must not reinterpret
already-persisted payloads. Use versioned bindings and retain old handlers until
all their work and required result lookups drain.

A useful exit is: standard replay-safe task → explicit external/local split →
selected native projection/operation/schedule, while callers either retain a
truthful result contract or migrate to a richer one. If the adapter insists that
all obligations remain arbitrary functions with four task statuses, stop there;
it cannot expose Due's evidence model honestly.


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
