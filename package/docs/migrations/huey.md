# Coming from Huey

Huey's compact task, result and scheduling API is a good fit for many applications.
Consider Due for selected tasks that have grown their own recovery and product
state coordination. Keep the rest on Huey; a task queue does not need replacing
simply because a richer lifecycle is available.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due can record the product change and its obligation in one PostgreSQL
transaction, recover a lost wakeup and fence local publication by captured
revision. Scheduled-action and evidence-required modes can replace task-specific
reschedule/revoke or uncertain-provider protocols when those semantics fit.

Huey already offers retries, scheduling and result handles. Stay if those are
sufficient, or if its lightweight deployment/storage choices are important.
Due adds Django/PostgreSQL, concrete work models, Celery and a broker; that cost
needs a demonstrated product reliability benefit.

## Prepare the Due side

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


## The interfaces you will work with

| Familiar primitive | Its strength | Due counterpart and semantic change |
| --- | --- | --- |
| `@huey.task`, calling a decorated function | Compact submission API with retries | Explicit application admission function and capability |
| `Result` and result store | Fetch the output of an invocation | Canonical product output with separately inspectable work state |
| `.schedule()`, revoke/restore, task locks | Scheduling and consumer controls | New-work `not_before` or `ScheduledAction`; no general revoke/lock emulation |

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
from huey import RedisHuey
from .models import Document
from .storage import write_revision

huey = RedisHuey("publication")


@huey.task(retries=3, retry_delay=60)
def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_revision(text, publication_id)
    Document.objects.filter(pk=document_id).update(published_key=key)
    return key


result = publish(42, "Hello", "request-123")
key = result(blocking=True, timeout=30)
```

With Huey, `publish(...)` submits because the decorator wraps the function;
with Due, submission is deliberately visible in the producer's transaction.
The effect itself runs only when Due dispatches it. Keep direct calls to pure
storage helpers distinct from admitted background work.

A Huey `Result` can block for one invocation. Reading `Document.published_key`
in Due instead reads the last settled product value, which can remain present
while a newer revision is pending. The gain is that this pointer only changes
under the current work's fence, not that polling becomes a result future.

## After: model the obligation, split execution from completion

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


## Delayed delivery versus a movable deadline

With a timezone-aware `deadline`, the familiar delayed call is:

```python
publish.schedule(args=(42, "Hello", "request-123"), eta=deadline)
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
| Decorated task invocation | Explicit transactional producer and captured work |
| Result handle / result group | Canonical product output or per-occurrence result; preserve consumers deliberately |
| Retries / retry delay | Due policy plus explicit terminal and uncertain outcomes |
| `.schedule()` | Aware fixed delay or a native scheduled action with eligibility checks |
| Revoke/restore | Define cancellation meaning; pending cancellation cannot unsend external I/O |
| Task locks | Identify the serialization domain; per-work ownership is not a global lock |
| Periodic task / pipeline | Keep Huey scheduling or persist occurrences and successor obligations explicitly |

## Pay particular attention to

A canceled task and an operation proven not to have happened are different
facts. When moving a revoke-based deadline to Due, handle an already-started
operation visibly rather than returning a false promise of cancellation.

Do not retain a required external follow-up only in a success callback. Admit it
as another obligation in local settlement. Likewise, cleanup in `finally` does
not survive a killed process; use persisted compensation when cleanup is owed.

## Cutover and rollback

Deploy Due schema, registration, workers and recurring recovery before moving
one producer. Drain queued, scheduled and retrying Huey tasks, retain result
storage for existing handles, and keep periodic production with exactly one
owner. Account for revoked tasks whose restore policy still makes them relevant.

Import only through a reviewed occurrence mapping with the old execution path
quiesced; preserve provider identity and unresolved outcomes. Roll back new
admissions independently while Due resolves its existing cohort. Do not execute
both paths to compare their side effects.

## Can a thin adapter help?

A bound task-call or fixed-delay facade can help a few call sites; none ships.
Result objects, revoke/restore, locks and pipelines are separate contracts,
not consequences of copying the decorator spelling. Keep the native capability
visible so richer scheduling, evidence and completion can be adopted. If the
facade must reproduce Huey's whole result/scheduler model, direct call-site
migration is likely easier to maintain.

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

Exercise Result consumers, lock contention, delayed-time handling, revoke versus
already-started execution, periodic occurrence identity, pipeline handoff and
worker death during cleanup.

## Sources

[Huey guide](https://huey.readthedocs.io/en/latest/guide.html), [recipes](https://huey.readthedocs.io/en/stable/recipes.html).
