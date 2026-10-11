# Coming from arq

arq is built around async functions and Redis jobs. Due is a Django-backed
obligation runtime executed through Celery. Migrate only when a product lifecycle
benefit justifies that runtime change; an async application does not gain by
replacing its enqueue spelling alone.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due can tie admission to a Django product transaction, distinguish changing
inputs from execution attempts, and recover fenced local completion. Its
non-repeatable operation modes explicitly retain uncertain provider outcomes.
Those can remove application recovery code around an arq job.

arq already expects jobs may run more than once under its pessimistic execution
model. Due does not eliminate the replay problem for arbitrary async functions.
Stay with arq for an async-first worker, shared event-loop resources and Redis
job/result behavior that already meet the requirement.

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
| Coroutine in `WorkerSettings.functions` | Native asyncio jobs with worker context | Synchronous capability callback with an explicit async bridge if needed |
| `ArqRedis.enqueue_job()` → `Job` | Redis admission, explicit job IDs and deferral | Atomic Django producer returning captured work |
| `Job.result()`, abort, expiry, `ctx` | Per-job result/cancellation and shared async resources | Domain outcomes and work policy; no automatic ctx/abort contract |

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
import asyncio
from typing import Any
from arq.connections import ArqRedis
from .models import Document
from .storage import write_revision


async def publish(ctx: dict[str, Any], document_id: int, text: str, publication_id: str) -> str:
    key = await asyncio.to_thread(write_revision, text, publication_id)
    await Document.objects.filter(pk=document_id).aupdate(published_key=key)
    return key


class WorkerSettings:
    functions = [publish]


async def submit(redis: ArqRedis) -> str:
    job = await redis.enqueue_job("publish", 42, "Hello", "request-123")
    assert job is not None  # A duplicate explicit job ID may return None.
    key = await job.result(timeout=30)
    assert isinstance(key, str)
    return key
```

An arq coroutine usually receives resources in `ctx`. Due does not supply
that context or preserve the worker event loop. For an async provider helper,
call `run_effect_coroutine(async_helper, ...)` from `perform_effect` and create
resources with the correct loop lifetime; do not pass a coroutine object or
reuse a client tied to the old arq loop.

For an async HTTP caller, wrap the whole synchronous Due producer with
`sync_to_async(request_publication, thread_sensitive=True)` and await it. Keep
the transaction together. The gain is transactionally captured inputs and fenced
product completion; losing a native async worker model may outweigh it.

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

## Translate the contracts, not just the call

| Existing concept | Due migration decision |
| --- | --- |
| Registered async function name | Named capability retaining a compatible provider helper |
| Worker `ctx` / startup resources | Recreate typed worker-local dependencies; do not reuse an event-loop-bound client across processes |
| `_job_id` and uniqueness lifetime | Explicit occurrence key and retention; Due coalescing is a different contract |
| `_defer_until` / `_defer_by` | Normalize to aware `not_before`; scheduled-action mode for mutable deadlines |
| Job result / expiry | Persist required per-occurrence output; work/evidence retention is not result expiry |
| Retry and max tries | One Due retry authority, with tested counts and error disposition |
| Abort and cron | Specify pending cancellation and recurring identities separately; abort cannot retract provider acceptance |

## Pay particular attention to

The provider coroutine may be reusable, but the old worker's context lifecycle
is not transferred with it. Audit connection pools, HTTP clients, shutdown hooks
and cancellation behavior before choosing how to run it in the destination.
Due exposes `run_effect_coroutine` for a coroutine boundary; this is not an arq
worker or arbitrary event-loop compatibility guarantee.

If a job ID can be reused after its result expires, decide whether that means a
new business occurrence. Preserve original provider keys for existing work;
using a new Due-generated key can repeat an already-applied request.

## Cutover and rollback

Install Due and verify worker/recovery behavior independently first. Route new
occurrences of one function to it; let arq finish queued, in-progress, deferred
and retrying jobs with their existing contexts and result retention. Keep cron
ownership explicit during coexistence.

Quiesce old execution before any import, preserve job-to-occurrence mappings and
unknown provider outcomes, and do not move opaque runtime context into a payload.
Rollback changes new routing while Due remains responsible for admitted work.
Retain Redis results and old handlers until their consumers and delayed jobs drain.

## Can a thin adapter help?

A bound async admission facade is possible, but unimplemented. It could await
one transactional producer while rejecting unsupported arq options. It would not
provide WorkerSettings, `ctx`, abort or Redis Job semantics automatically.
A useful migration exposes native settlement/evidence; if all old async worker
semantics must remain invisible behind the facade, keeping arq is usually the
clearer choice.

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

Verify event-loop/resource lifetimes, coroutine cancellation after provider
acceptance, repeated job IDs across expiry, async result consumers and atomic
admission from the actual async request path.

## Sources

[arq 0.28 documentation](https://arq-docs.helpmanual.io/), [Due coroutine boundary](../../src/due/django/runtime/transport.py).
