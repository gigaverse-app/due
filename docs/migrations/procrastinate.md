# Coming from Procrastinate

Procrastinate already gives you PostgreSQL-backed jobs, transactional deferral
and useful locking semantics. Consider Due when your application also carries
code to reject stale completion, reconcile uncertain provider requests, or finish
local state after the external operation succeeded. The migration should remove
that code and preserve its guarantees.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md) · [Runnable comparison and shared failure tests](../../examples/comparison/README.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain—and when to stay

| Your application today | Benefit of adopting Due | Work you still own |
| --- | --- | --- |
| Task succeeds externally but crashes before publishing product state | Evidence-backed operations can finish local settlement from a retained receipt | A correlated provider result and receipt model |
| An older task publishes over a newer desired state | Captured revisions fence local publication; projection mode provides explicit repair policy | Remote ordering or replay-safe repair |
| Each task family has a stalled-job recovery protocol | Registered recovery owns work discovery, lease expiry and visible stopping | Policy, worker/scheduler operation and alerts |
| Reliability checks are repeated across tasks | Generated application histories exercise shared coordination | Real provider and business-result assertions |

Stay with Procrastinate when durable function execution and its lock semantics
already solve the problem. Due adds Django, Celery and a broker; it is not a
simpler PostgreSQL-only deployment. Transactional enqueue is shared ground,
not a new guarantee gained by switching.

## Prepare the Due side

Due currently requires **Python 3.14+, Django >=6.0.8,<7, PostgreSQL, Celery,
a broker, workers and periodic recovery**. It is alpha; pin a reviewed GitHub
revision. Product, work and evidence must use the same writable database through
Django's default connection.

Choose the capability by the external contract: `ReplaySafeEffect` for safe
repetition, `Projection` for mutable external state, `ScheduledAction` for a
movable deadline, or `DurableExternalOperation` / `ObservedExternalOperation`
when a possibly applied request must wait for evidence. See the
[capability imports and examples](../capabilities.md); none makes an arbitrary
provider call safe to repeat.

1. Add `due.django` to `INSTALLED_APPS` and apply its migrations.
2. Define a product owner and concrete work model derived from
   `UniverseDurableWork`, with captured input fields and `durable_work_schema`.
   Apply your app's migration too.
3. Implement the external operation and its local completion separately. Register
   the capability in web, worker, Beat and management-command processes.
4. Include `due.django.runtime.tasks` in Celery imports, merge the generated
   recovery schedules, and run workers for the registered queue. Monitor overdue,
   stopped and UNKNOWN work, plus the recovery heartbeat.

The [integration reference](../integration.md) has the exact wiring. The
[document example](../../examples/documents/effects.py), its
[models](../../examples/documents/models.py) and
[registration](../../examples/documents/apps.py) form a complete implementation;
reuse that structure instead of inventing a queue wrapper. The `examples` package
belongs to this repository, not the installed Due runtime. In your application,
implement the corresponding producer and models in your own app.

## The interfaces you will work with

| Familiar primitive | Its strength | Due counterpart and semantic change |
| --- | --- | --- |
| `Task` / `@app.task`, `.defer()` | Small function API with PostgreSQL admission | `ReplaySafeEffect[Document, PublicationWork, str]` plus explicit transactional admission |
| `Job` kwargs and job ID | Each deferred invocation has its own identity | Typed `PublicationWork` fields capture inputs; owner identity chooses coalescing versus occurrences |
| `lock`, `queueing_lock` | Cross-task execution serialization and queued duplicate rejection | Per-work claim/revision only; these lock contracts need separate design |

## Before: the task and its caller

The comparison publishes document 42's text to an immutable object and records
its key in `Document.published_key`. The source example's application helper
`write_revision(text, publication_id)` writes the same bytes to a stable key on
retries and returns that key. `publication_id` is allocated once per request,
not on each retry. This helper is application code, not a library API; the Due
implementation below shows the actual file-write body.

These are focused integration excerpts: put definitions in importable app
modules, configure the source worker/backend, and provision the `Document` row.
The deliberately simple source task has no application-specific stale-write
fence. If yours already has one, preserve its contract and compare what code Due
would replace; the source library is not inherently unable to implement it.

```python
from procrastinate.contrib.django import app
from .models import Document
from .storage import write_revision


@app.task
def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_revision(text, publication_id)
    Document.objects.filter(pk=document_id).update(published_key=key)
    return key


job_id = publish.configure(queue="publication").defer(
    document_id=42,
    text="Hello",
    publication_id="request-123",
)
```

`publish.configure(lock="document:42").defer(...)` serializes jobs sharing
that lock. Due's one owner/work pair does **not** reproduce that cross-task
ordering. `queueing_lock` can reject a duplicate with `AlreadyEnqueued`; the
Due producer below instead returns the existing obligation for equal inputs.
If your caller depends on rejection, changing the call changes its contract.

The gain here is the fenced completion boundary, not moving job storage to
PostgreSQL: Procrastinate already has that, including connection-bound atomic
deferral. Preserve that existing atomicity when rewriting the producer.

## After: model the obligation, split execution from completion

In your application's `models.py`, add a concrete work model. This example's
`OneToOneField` makes the document's **current publication** the obligation.
Use a distinct persisted request owner instead if every call must execute.
The fields below are from the [runnable models](../../examples/documents/models.py);
keep your existing `Document` model and migrate any added fields.

```python
from typing import ClassVar

from django.db import models

from due.django.models import UniverseDurableWork, durable_work_schema


class Document(models.Model):
    text = models.TextField(default="")
    publication_failed = models.BooleanField(default=False)
    published_key = models.CharField(max_length=64, blank=True)


class PublicationWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(Document, on_delete=models.SET_NULL, null=True)
    text = models.TextField()

    class Meta:
        indexes, constraints = durable_work_schema("document_publication")
```

In `effects.py`, replace the decorated task with the following capability and
producer. This is the [runnable effect](../../examples/documents/effects.py),
with its import changed to your application. The type parameters are owner,
captured work and effect result; here the provider returns a `str` key.

```python
import hashlib
import os
from pathlib import Path

from due.django.public import DurableWorkPolicy, DurableWorkState, ReplaySafeEffect
from .models import Document, PublicationWork


class PublishDocument(ReplaySafeEffect[Document, PublicationWork, str]):
    def perform_effect(self, work: PublicationWork) -> str:
        # Each revision writes its own immutable key. A late old worker cannot
        # overwrite the current revision's bytes. Retrying this key is safe.
        key = hashlib.sha256(work.provider_idempotency_key().encode()).hexdigest()
        output = Path(os.environ.get("DUE_OUTPUT_DIR", "/tmp/due-documents"))
        output.mkdir(parents=True, exist_ok=True)
        (output / key).write_text(work.text)
        return key

    def finalize_success(self, work: PublicationWork, result: str) -> None:
        # Due holds the owner/work locks and validates the captured revision
        # and fencing token before publishing this pointer.
        Document.objects.filter(pk=work.owner_id).update(published_key=result, publication_failed=False)

    def finalize_retry(self, work: PublicationWork, error: Exception) -> None:
        if work.state == DurableWorkState.EXHAUSTED:
            Document.objects.filter(pk=work.owner_id).update(publication_failed=True)


publish_document = PublishDocument.for_owner(owner=Document, work=PublicationWork, policy=DurableWorkPolicy())


def request_publication(document_id: int, text: str) -> PublicationWork:
    """Commit product intent and its obligation together; wake only after commit."""
    with publish_document.atomic(document_id) as pending:
        document = pending.locked_owner
        document.text = text
        document.publication_failed = False
        document.save(update_fields=["text", "publication_failed"])
        return pending.request_work(PublicationWork(text=text, universe="example"))
```

The filesystem stands in for replay-safe object storage. For multiple worker
hosts use shared storage or your provider API with the same immutable-key
contract. A hash does not make an arbitrary payment/email/API request idempotent.
Do not generate a fresh provider key on each attempt.

The caller becomes:

```python
from .effects import request_publication
from .models import Document, PublicationWork

work = request_publication(42, "Hello")  # Admission; not completed publication.

# A later status read, not a blocking result/future API:
state = PublicationWork.objects.get(pk=work.pk).state
key = Document.objects.get(pk=42).published_key
```

`key` can still be the previous settled publication while newer work is pending.
The separate reads above are illustrative; expose a domain status endpoint with
consistent reads and revision identity if callers must correlate a particular
request. A work row can cover several revisions and is not a historical result
per submission. Apply the schema migration and registration described above
before sending work; defining the class alone does not start execution.

### What changed, and where the gain comes from

| Code change | Runtime meaning | Concrete gain or cost |
| --- | --- | --- |
| Function kwargs → `PublicationWork.text` | The captured input belongs to persisted work | The worker need not reread mutable document text to reconstruct what was owed |
| Enqueue call → `atomic()` plus `request_work()` | Product text and obligation commit or roll back together | Lost wake-up is recoverable from the database; upstream transactional enqueue may already provide atomicity |
| Storage call → `perform_effect()` | External work runs outside local settlement and may repeat | Reuses your provider code, but still requires a proven replay-safe contract |
| ORM update → `finalize_success()` | Due validates captured revision and ownership before publishing the pointer | An older attempt cannot publish over a newer local revision |
| Invocation result → product output and work state | Completion is a domain state transition | More explicit product semantics; callers lose a drop-in job-result API |

Try two edits: request text **A**, then **B**, and let A's worker finish last.
They write different immutable keys. Due rejects A's stale local settlement, so
it cannot replace B's product pointer. This does not kill A's provider request or
protect a mutable remote key. Requesting **B** again with unchanged input
preserves the obligation's outcome/budget; it is not another event and does not
restart stopped work. A deliberate recovery action uses `pending.retry_failed()`
where supported, rather than ordinary resubmission.

For a crash after storage succeeds but before local completion, this replay-safe
example may write the same revision again and then settle. It does **not** retain
a generic return-value checkpoint. If repetition is unsafe, choose an evidence-
backed operation instead; the operation must retain a correlated receipt and keep
an uncertain outcome `UNKNOWN` until resolved. That is a different capability,
not a retry flag on this example.

## Delayed delivery versus a movable deadline

With a timezone-aware `deadline`, the familiar delayed call is:

```python
publish.configure(schedule_at=deadline).defer(
    document_id=42,
    text="Hello",
    publication_id="request-123",
)
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

## Map the Procrastinate contract

First classify `.task`, `.defer[_async]`, `.configure`, retry configuration,
periodic tasks and all `lock`/`queueing_lock` uses. Procrastinate's supported
connection-bound deferral already offers atomic admission; preserve that property
when moving to Due's Django transaction.
[Source contract](https://procrastinate.readthedocs.io/en/stable/howto/production/external_connection.html).

| Procrastinate behavior | Due migration |
| --- | --- |
| Registered task and kwargs | Typed captured work plus a registered capability; retain the useful pure/provider helper |
| `.defer(...)` creates a job | Call the new application admission function inside the product transaction; choose occurrence versus desired-state identity |
| `.configure(connection=...)` | Move the product write onto Due's supported Django transaction, or stay on Procrastinate; opening another connection is not atomic |
| Execution `lock` | Preserve the shared serialization domain explicitly; per-work claims do not reproduce cross-task lock ordering |
| `queueing_lock` / `AlreadyEnqueued` | Decide whether the caller requires rejection or coalescing; ordinary `request_work` does not reproduce that exception contract |
| `schedule_at` / `schedule_in` | For a fixed delay, `pending.request_work(..., not_before=aware_datetime)`; use `ScheduledAction` if the deadline can move or become unnecessary |
| Periodic task | Persist each required occurrence, or deliberately define reconciliation as latest state; a Due recovery schedule is not the original cron job |
| Retry/stalled jobs | Translate exception classes, delays and budgets into Due policy; retain recovery for old jobs until drained |

For fixed delays, `not_before` controls admission of new work; repeating unchanged
input is not a general reschedule command. Use the schedule capability's
`schedule_at`/`cancel_pending` operations for mutable deadlines and handle their
`ALREADY_STARTED` result instead of claiming cancellation succeeded.

Do not infer strict external serialization from an owner lock or local revision
fence. A prior provider operation can outlive its lease. If all operations sharing
a Procrastinate lock must execute in order, a migration that merely creates
independent Due owners is incorrect. Stay on the old path until this requirement
has a proven replacement.

Start with an unlocked replay-safe task. Verify rollback, lost notification,
worker death, two identical submissions, and two changed submissions before
porting locked or non-repeatable tasks. Keep old workers and stalled-job recovery
for old IDs; do not let both engines retry an imported occurrence.

## Cut over and retain a way back

Deploy the Due schema, registrations, workers and recovery before changing the
selected producer. Prefer routing only new occurrences to Due while existing
Procrastinate jobs drain. Inventory queued, doing, scheduled, failed and stalled
jobs, including jobs waiting behind a shared lock. Preserve task names and
stalled-job recovery for that cohort.

If copying jobs is unavoidable, quiesce their producers, execution and retry
paths first. Keep a unique mapping of old job IDs and original provider keys;
an in-flight request is not safe to import as executable work merely because its
worker stopped. Keep unknown outcomes with their existing evidence owner until
a domain-specific transfer is proven.

To roll back, route new requests back to Procrastinate while Due completes what
it already owns. Do not give both engines permission to retry the same operation.
Retire old rows and routes only after required result lookups and redeliveries
are accounted for.

## Could you keep `.defer()`?

A facade for explicitly bound task names and keyword arguments is feasible, but
none ships today. Reject unsupported options before admission. `lock`,
`queueing_lock`, `AlreadyEnqueued`, priority and `connection=` need their actual
semantics preserved; they are not aliases for Due's per-work claim or revision.
An integer return value alone does not provide Procrastinate's job-manager API.

The facade is useful if it leads to a native capability with an explicit external
call, settlement and replay/evidence policy. It is a dead end if it keeps those
boundaries hidden inside an unchanged function or reconstructs Procrastinate's
lock engine just to preserve spelling.

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

Also test cross-task lock ordering, queueing-lock rejection, scheduled jobs
blocking the same lock, and two identical submissions. A migration that passes
Due's normal claim tests can still break these Procrastinate contracts.

## Sources

[Deferral](https://procrastinate.readthedocs.io/en/stable/howto/basics/defer.html),
[execution locks](https://procrastinate.readthedocs.io/en/stable/howto/advanced/locks.html),
[queueing locks](https://procrastinate.readthedocs.io/en/stable/howto/advanced/queueing_locks.html),
[stalled recovery](https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html).
