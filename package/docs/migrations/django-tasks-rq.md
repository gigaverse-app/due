# Coming from django-tasks-rq

django-tasks-rq combines Django's task frontend with RQ execution. You have two
contracts to preserve: TaskResult and task options at the frontend, plus RQ
storage, delivery and operations underneath. Due adoption should improve one
selected product lifecycle without pretending both contracts disappear.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due can move product/work admission into one PostgreSQL transaction and recover
a lost Redis/Celery wakeup. Captured revisions and evidence-required operation
modes can replace custom stale-completion and uncertain-effect handling.
These gains require native admission and a split external/local task body.

Stay on django-tasks-rq when the standard task API and RQ execution already meet
your needs. Due has no backend implementation for that API today, and adopting
it adds work models, migrations and Celery operation. It does not automatically
remove Redis or preserve standard result objects.

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
from django_tasks import task
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

The example uses the `django_tasks` frontend with the RQ backend selected in
`TASKS`. It returns `TaskResult`, not RQ's raw `Job`; inventory callers of both
interfaces if your code reaches through to RQ. Due currently supplies neither
this backend nor a replacement `TaskResult` implementation.

For the migrated path, product changes and the work row share a PostgreSQL
commit. The RQ worker and result lookup remain necessary for already queued
invocations. A new work ID cannot be passed into either the old backend's result
lookup or RQ's job lookup.

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

## Translate the contracts, not just the call

| Existing concept | Due migration decision |
| --- | --- |
| Django `Task` / `.enqueue` | Native application producer today; no Due backend setting exists |
| TaskResult / return value / errors | Canonical result or explicit projection, with old lookup retained |
| RQ-backed storage and worker | Retain for old task IDs and messages; add Due worker/recovery for new work |
| Backend alias and queue selection | Keep source routing while mapping selected work to a declared Due route |
| `run_after`, priority and context | Audit the installed backend’s support; do not silently drop requested behavior |
| Legacy bundled package path | Preserve historical schema/import compatibility during the package split |
| Retry and retention | One retry owner per occurrence; result TTL cannot remove unresolved evidence |

## Pay particular attention to

A framework task API does not make a Redis enqueue atomic with a PostgreSQL
product update. Move durable Due admission inside the product transaction;
leaving it in `on_commit` retains the gap before admission. If an existing outbox
already covers this, preserve its guarantees until the replacement is proven.

Keep frontend result IDs distinguishable by backend during coexistence. Native
UNKNOWN requires a richer obligation view; translating it into an ordinary
FAILED result can cause a consumer to repeat a possibly applied operation.

## Cutover and rollback

Deploy Due schema, capability registration, workers and recovery. Change only
new admissions for the selected task; keep the original backend alias and RQ
workers for old ready, started, deferred, scheduled and failed jobs. Retain task
code and result lookups for outstanding frontend handles.

A transferred job needs a unique source-to-occurrence mapping, original provider
key and a stopped old retry path. Unknown effects are not fresh work. Rollback
changes new routing while Due remains responsible for its admitted cohort.
Do not remove the RQ backend or result storage until its callers have drained.

## Can a thin adapter help?

The strongest future route is a Django Tasks backend, rather than emulating
RQ internals behind it. That backend still needs real TaskResult fidelity,
invocation identity and unsupported-option validation; it is unimplemented.
A minimal named producer facade can help now only if callers deliberately accept
native results.

Keep access to native evidence, scheduling and fenced completion. The optional
[frontend backend design](django-tasks.md#could-you-keep-the-standard-task-interface)
explains why generic four-status tasks must not conceal uncertain operations.

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

Test frontend result refresh/errors and backend-aware lookup, RQ delayed/retry
cohorts, transaction rollback, two equal submissions, context/priority options
and rollback routing. Verify every old TaskResult still resolves to its original
execution owner.

## Sources

[django-tasks-rq](https://github.com/RealOrangeOne/django-tasks-rq), [Django Tasks API](https://docs.djangoproject.com/en/6.0/ref/tasks/), [RQ jobs](https://python-rq.org/docs/jobs/).
