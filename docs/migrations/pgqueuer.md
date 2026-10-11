# Coming from PgQueuer

PgQueuer is a natural choice for PostgreSQL-backed async jobs with transactional
enqueue. Due becomes interesting when a handler also needs a durable business
lifecycle: changing desired inputs, provider uncertainty, or recoverable local
completion. Preserve PgQueuer when its existing execution model fits better.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due can replace per-handler claim/reconciliation code with registered recovery
and fenced settlement. For a non-repeatable operation, its evidence model makes
“unknown” a state to resolve instead of a reason to rerun the handler. Generated
application histories can check the real admission and settlement boundaries.

Transactional enqueue and durable jobs are already PgQueuer strengths. Due does
not improve them merely by also using PostgreSQL. Stay with PgQueuer for an
async-first application that wants a small database-only queue, serialized
entrypoints, its scheduling or completion watcher without adopting Django/Celery.

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
| `@pgq.entrypoint`, `Job.payload` | Async handlers over an explicit bytes payload | Typed model fields consumed by `perform_effect` |
| `Queries.enqueue()` → job ID list | Connection-bound PostgreSQL enqueue and batching | Django transaction with one selected owner/work pair |
| Entrypoint concurrency/serialization | Control dispatch across handler executions | Per-work ownership only; retain broader flow control separately |

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
from pydantic import BaseModel
from pgqueuer.models import Job
from pgqueuer.queries import Queries
from pgqueuer.types import JobId
from .models import Document
from .storage import write_revision


class PublicationInput(BaseModel):
    document_id: int
    text: str
    publication_id: str


# pgq is the application's configured PgQueuer instance.
@pgq.entrypoint("publish")
async def publish(job: Job) -> None:
    assert job.payload is not None
    data = PublicationInput.model_validate_json(job.payload)
    key = await asyncio.to_thread(write_revision, data.text, data.publication_id)
    await Document.objects.filter(pk=data.document_id).aupdate(published_key=key)


async def submit(queries: Queries) -> list[JobId]:
    payload = PublicationInput(document_id=42, text="Hello", publication_id="request-123")
    return await queries.enqueue("publish", payload.model_dump_json().encode())
```

Payload parsing moves from the entrypoint into admission and concrete model
fields. If bytes are still arriving from another system, keep one validation
boundary there; do not enqueue arbitrary serialized callables into Due.

PgQueuer's async connection transaction does not automatically join
`publish_document.atomic()`. Move the relevant product writes into the shown
Django transaction, or retain PgQueuer. The gain is fenced settlement and a
shared business-recovery protocol; PostgreSQL persistence is already present.
Async client lifetime also changes: the native Due effect below is synchronous.

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
| Entrypoint and `Job.payload` | Canonical captured fields plus external execution and local completion methods |
| Connection-bound `Queries.enqueue` | Product and obligation must move together to Django’s default transaction |
| Batch enqueue | Choose all-or-nothing versus partial admission and preserve it explicitly |
| Serialized dispatch / per-entrypoint limits | No automatic equivalent in per-work claims; retain or implement the required gate |
| `execute_after` and recurring schedules | Fixed delay to aware `not_before`; recurring business occurrences need their own durable identities |
| Completion watcher | Observe canonical product outcomes; admission is not completed work |
| Shared async resources | Provide compatible worker-local clients; source event-loop resources cannot simply cross processes |

## Pay particular attention to

Sharing a PostgreSQL server does not make an asyncpg transaction and a Django
transaction atomic. A small enqueue wrapper on a second connection would lose a
property PgQueuer already gives you. If moving the product write is not possible,
keep that workflow on PgQueuer or design an explicit durable bridge.

Likewise, one claim per work row does not preserve serialized dispatch across an
entire entrypoint. Decide whether every queued payload must execute or only the
latest desired state; the latter is a product change, not a queue optimization.

## Cutover and rollback

Bring up Due workers, registered recovery and migrations first. Route only new
occurrences for one entrypoint to Due and let PgQueuer finish its prior cohort.
Keep its completion tracking for existing callers and its scheduler for unmigrated
entrypoints. Inventory deferred and retrying payloads as well as ready ones.

Transferring a cohort requires quiescing the old dispatch/retry paths, retaining
payload/occurrence identities and preserving provider keys. Do not infer that a
picked job never acted. Rollback routes new work back while Due resolves what it
already owns; never independently redeliver the same occurrence in both engines.

## Can a thin adapter help?

A named enqueue converter could be thin. A replacement driver promising
transaction participation, batching, serialized entrypoints and completion
watchers would not be. No adapter ships today. Keep native capability access
available so callers can progress from payload submission to explicit settlement
and evidence; otherwise the extra stack buys little beyond familiar syntax.

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

Exercise transaction rollback on the real connection, batch partial failure,
serialized entrypoints, payload decoding/versioning, shared-resource startup,
and completion observers waiting on the correct occurrence.

## Sources

[PgQueuer source and guide index](https://github.com/janbjorge/pgqueuer/tree/3064102fa7a478987aefb0ff2f6e0ed6d1adaaa0).
