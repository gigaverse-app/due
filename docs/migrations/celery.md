# Coming from Celery

Keep Celery. Due uses it to execute work and adds a durable obligation lifecycle
around selected business operations. This migration replaces task-specific
outbox, claim, reaper and settlement code; it does not replace the Celery worker
fleet or require moving all tasks at once.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## Where adoption pays off

If a product row commits and `.delay()` never reaches the broker, a post-commit
callback alone cannot recover the missing request. Due admits the work with the
product and periodically rediscovers it. If an old worker returns after a newer
revision, fenced settlement prevents it publishing current local completion.
Evidence-required modes separately handle the case where the provider may have
acted but the reply was lost.

These are useful when your tasks already need custom recovery and coordination.
Keep independent computation and workflows that rely on Canvas or result backends
on their existing paths. Celery already provides retries, routing and scheduling;
Due adds a particular product lifecycle, not universally better task execution.

## Executable comparison

The [document-publication add-on](https://github.com/gigaverse-app/due/blob/main/examples/celery_migration/README.md) runs
a plain Celery task beside the existing Due implementation with the same model
and file provider. It includes real-worker delivery, a replay control and two
explicit baseline gaps for lost delivery and stale completion. It also names the
extra schema and recovery machinery, rather than claiming the port is shorter.

## Prepare the Due side

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


## The interfaces you will work with

| Familiar primitive | Its strength | Due counterpart and semantic change |
| --- | --- | --- |
| `Task`, `.delay()` / `.apply_async()` | Flexible delivery/routing and a mature worker system | Keep Celery workers; declare a Due capability for business completion |
| `AsyncResult`, `self.retry()` | Per-task results and programmable retries | Domain output plus `DurableWorkPolicy` and work state |
| Signatures, chains, groups, chords | Compose distributed task execution | No drop-in canvas; retain it or explicitly model successor obligations |

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
from celery import shared_task
from .models import Document
from .storage import write_revision


@shared_task(autoretry_for=(OSError,), retry_kwargs={"max_retries": 3})
def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_revision(text, publication_id)
    Document.objects.filter(pk=document_id).update(published_key=key)
    return key


result = publish.delay(42, "Hello", "request-123")
# From a result-consuming client, with a result backend configured:
key = result.get(timeout=30)
```

You retain Celery as transport. What changes is the task boundary: the provider
call becomes `perform_effect`; the ORM update becomes `finalize_success`.
Wrapping the whole old task body in `perform_effect` would miss the local fence.

`result.get()` waits for a task result; `request_publication()` returns after
admission and must not be reported as completed publication. Likewise, moving
`self.retry()` into the effect would create competing retry owners. Configure
Due's policy for the migrated obligation and keep unrelated Celery tasks intact.
The gain is recovery from committed business intent even if the post-commit
message is lost; `transaction.on_commit(publish.delay)` alone lacks that record.

## After: model the obligation, split execution from completion

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


## Delayed delivery versus a movable deadline

With a timezone-aware `deadline`, the familiar delayed call is:

```python
publish.apply_async(args=(42, "Hello", "request-123"), eta=deadline)
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

## Map the Celery contract

Keep the Celery deployment. Register Due's runtime tasks and generated recovery
schedules in every relevant process. Move the chosen task's retry/reaper/claim
mechanism into Due and leave its reusable provider helper outside the task
wrapper. Do not let both Celery autoretry and Due schedule independent retries
for the same obligation.

Replace `on_commit(lambda: task.delay(...))` with transactional work admission;
the post-commit Due wake is an optimization backed by recovery. Preserve unrelated
Beat entries. A periodic recovery tick does not implement a business cron event.
`acks_late`, revocation and retries do not establish provider nonapplication.
[Celery task contract](https://docs.celeryq.dev/en/stable/userguide/tasks.html).

Chains, groups, chords and `AsyncResult.get()` require explicit redesign or stay
on Celery. Prefer passing a durable application occurrence ID to a thin legacy
entry point during transition; it should request progress on an already-admitted
obligation, not admit a second independent operation. Retain old task names while
messages can still arrive.

| Existing setting or behavior | Migration decision |
| --- | --- |
| `retry`, `autoretry_for`, backoff | One owner schedules retries: the Due capability policy |
| ETA/countdown | Fixed admission delay uses aware `not_before`; mutable deadlines use `ScheduledAction` |
| Revocation/termination | Local cancellation cannot prove an already-sent provider request did not act |
| Per-call queue/priority | Due's registered route is fixed per work model; do not silently discard options |
| Success callback sends another task | Admit successor work in fenced settlement |
| Chain/group/chord | Preserve Canvas or design explicit phase state and handoff; `Dependent` is not Canvas |
| Result backend/expiry | Keep old lookup for old IDs; store new domain results with deliberate retention |

## Cutover and rollback

Deploy registrations and recovery before redirecting one producer. Run legacy
messages under their existing task names until drained; Due and ordinary Celery
tasks can coexist in the same deployment. Disable the old recovery/retry path
only for obligations now owned by Due. Keep unrelated Beat entries intact.

Never dual-publish the old effect and new Due work to compare results. Shadow
read-only decisions instead. If an old task becomes a compatibility entry point,
it must locate a stable admitted occurrence and request progress, not create a
new occurrence each time it is redelivered.

Rollback redirects new admissions while Due continues to own already-committed
work. Keep its schema, worker routes, evidence and recovery until that work is
resolved. An old provider request with uncertain outcome must not be restarted
with a newly generated idempotency key.

## Should you keep `.delay()` through a shim?

Keeping a few producer entry points can reduce call-site changes. It does not
provide AsyncResult, Canvas, task request context or revoke semantics. No Due
Celery-API shim ships. Since Celery is already the transport, a clearly named
application admission function usually makes the new transaction boundary easier
to understand than another general decorator.

The transition remains useful if the native capability is accessible and old
producer methods simply delegate to it. It stops helping when compatibility
requires a second retry authority or hides UNKNOWN from operators.

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

Also prove that legacy redelivery cannot create a second occurrence, that only
one retry owner is active, and that successor admission survives a crash after
parent settlement. Exercise any retained result or Canvas consumers unchanged.
