# Coming from RQ

Start with an RQ job that owns a product-side effect: publish an artifact, update
a provider resource, or complete a requested operation. Due can make that
obligation part of the product transaction and keep completion recoverable after
a lost message or worker death. Keep ordinary jobs on RQ when its function-and-
result model is already sufficient.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What changes for the better

A PostgreSQL product update followed by Redis enqueue has two commit boundaries.
Due can replace that boundary with one product/work transaction; periodic
recovery supplies a lost wakeup. If you already have a correct transactional
outbox, the gain is replacing its maintenance burden, not inventing durability.

For overlapping publications, Due can prevent an older worker from publishing
current product completion. For non-repeatable requests, its operation modes
retain UNKNOWN until evidence settles the outcome. These gains require splitting
the task body and declaring the right contract; a callable wrapper cannot infer
whether an email, payment or provider request is safe to repeat.

Stay with RQ for independent computation, existing Job/result consumers or
substantial dependency graphs. Due requires a Django/PostgreSQL application and
Celery; switching is not a way to remove worker infrastructure or necessarily Redis.

## Prepare the Due side

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


## The interfaces you will work with

| Familiar primitive | Its strength | Due counterpart and semantic change |
| --- | --- | --- |
| `Queue.enqueue()` → `Job` | Enqueue an ordinary importable Python function | Declare a typed capability and admit `PublicationWork` |
| `Job.return_value()` / result retention | Per-invocation output and job inspection | Persist the business output in `Document`; no generic `Job` facade |
| `Retry`, dependencies, scheduled enqueue | Convenient execution policy around a function | `DurableWorkPolicy`, explicit successor obligations, and due times; no dependency-graph importer |

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
from redis import Redis
from rq import Queue, Retry
from .models import Document
from .storage import write_revision


def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_revision(text, publication_id)
    Document.objects.filter(pk=document_id).update(published_key=key)
    return key


queue = Queue("publication", connection=Redis())
job = queue.enqueue(
    publish,
    42,
    "Hello",
    "request-123",
    retry=Retry(max=3, interval=60),
)
# Later, after checking the job completed:
key = job.return_value()
```

`job.return_value()` asks for the output of one invocation. The Due code below
reads `Document.published_key`, the last settled publication for the document.
That is a deliberate API change. If callers need every historical result,
introduce a `PublicationRequest` owner per invocation and put the result there.

`Retry(max=3, interval=60)` is not a mechanical rename to
`DurableWorkPolicy(max_rearms=3, retry_delay=timedelta(seconds=60))`: Due's budget
covers rearming an obligation and recovery participates in that lifecycle.
Verify total attempts and worker-death behavior, not just matching the integers.
The potential gain is a PostgreSQL obligation committed with product changes;
Redis enqueue and a Django transaction do not themselves form that commit.

## After: model the obligation, split execution from completion

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


## Delayed delivery versus a movable deadline

With a timezone-aware `deadline`, the familiar delayed call is:

```python
queue.enqueue_at(deadline, publish, 42, "Hello", "request-123")
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

## Map the RQ contract

Inventory `Queue.enqueue[_at/_in]`, callable import paths, custom serializers,
`job_id`, `Retry`, callbacks, dependencies, `Job.meta`, result/failure retention
and cancellation. RQ's Redis job record is not Due's work row. A Redis pipeline
cannot join a PostgreSQL product transaction.
[Queue API](https://python-rq.org/docs/),
[jobs](https://python-rq.org/docs/jobs/),
[retry behavior](https://python-rq.org/docs/exceptions/).

Replace selected producers with the admission function above. Persist arguments
as declared model fields or a deliberately validated payload; do not blindly
unpickle old job payloads into a new generic executor. Put results required by
callers in canonical application state during settlement. Port a callback that
performs an effect to another obligation, and a dependency to an explicit
completion condition/handoff. `Dependent` wakes existing work; it does not create
RQ's dependency/result graph automatically.

Normalize deferred times explicitly: RQ accepts naive local datetimes, whereas
Due's `not_before` requires an aware datetime. Preserve the original intended
timezone when translating, especially across daylight-saving transitions.
[RQ scheduling](https://python-rq.org/docs/scheduling/).

Translate execution timeouts to provider timeouts and Due's registered worker
limits, with the lease longer than the hard worker limit. Translate retry counts
by tested histories, not matching numeric names: Due distinguishes delivery
reservation recovery from failed execution. Result TTL and the obligation's
retention have different meanings; retain uncertain evidence independently.

Drain the selected RQ queue/registry cohort before switching ownership. Scheduled,
deferred, failed and started jobs all need a disposition, not just the ready queue.
Keep old result lookup alive for IDs already returned to clients. Redis may still
be needed as Due's Celery broker, so this migration does not necessarily remove it.

## Cutover and rollback

Route a chosen set of new occurrences to Due after its worker and recovery are
live. Drain old ready, scheduled, deferred and started jobs under RQ; explicitly
dispose of failed jobs instead of silently resetting their retry budgets. Keep
old callable paths and result lookup for IDs returned before cutover.

If transferring jobs, stop their old execution/retry path, retain a unique
old-job-to-new-owner mapping and preserve original provider keys. Do not replay
a possibly applied operation as fresh work. During rollback, change routing for
new admissions only; Due must finish or deliberately transfer its admitted
obligations. Never have RQ and Due independently retry the same imported job.

## Could you keep `Queue.enqueue()`?

A facade for an allowlist of callables is possible; no adapter ships today.
It is small only when callers do not require a faithful Redis-backed `Job`.
Results, metadata, TTL, registries, callbacks, cancellation and dependencies make
full Job emulation a separate project. Unsupported options must fail before
persisting an invocation, not disappear silently.

A useful facade exposes the native capability and preserves each invocation's
identity. Upgrading selected jobs to explicit settlement or evidence handling
then remains possible. A facade that runs arbitrary old functions only fences
its own bookkeeping; the functions' internal product writes remain unprotected.

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

Exercise Job consumers, dependency completion, callback failure, custom serializer
round trips, two equal submissions, result expiry and delayed-time conversion.
Confirm every accepted old/new ID has one execution owner and a usable lookup.
