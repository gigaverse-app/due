# Coming from Dramatiq

Start with an actor whose retries are surrounded by custom product recovery
logic. Due can centralize that lifecycle while retaining the actor's useful
provider helper. Ordinary actors with well-understood idempotency, middleware
and result behavior can remain on Dramatiq.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

For an application that commits product state then sends a broker message,
Due can admit the obligation inside the database transaction and recover a lost
send. Captured revisions can prevent a late attempt publishing outdated product
completion. Evidence-required modes let uncertain effects wait for correlation
instead of treating every interruption as permission to replay.

Dramatiq already retries failed actors and documents duplicate delivery and
idempotency requirements. Due does not make a non-idempotent actor safe by
wrapping it. Stay with Dramatiq if actor execution and its middleware ecosystem
are the desired abstraction, especially outside Django/PostgreSQL.

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
[document example](https://github.com/gigaverse-app/due/blob/main/examples/documents/effects.py), its
[models](https://github.com/gigaverse-app/due/blob/main/examples/documents/models.py) and
[registration](https://github.com/gigaverse-app/due/blob/main/examples/documents/apps.py) form a complete implementation;
reuse that structure instead of inventing a queue wrapper. The `examples` package
belongs to this repository, not the installed Due runtime. In your application,
implement the corresponding producer and models in your own app.

## The interfaces you will work with

| Familiar primitive | Its strength | Due counterpart and semantic change |
| --- | --- | --- |
| `Actor`, `.send()` → `Message` | Simple actor dispatch with middleware | Capability declaration plus atomic owner/work producer |
| Retries middleware and message options | Reusable delivery/retry behavior | Due policy owns the migrated obligation through attempts |
| Results middleware, pipelines/groups | Optional result storage and composition | Domain results; no automatic middleware or pipeline translation |

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
import dramatiq
from .models import Document
from .storage import write_revision


@dramatiq.actor(max_retries=3)
def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_revision(text, publication_id)
    Document.objects.filter(pk=document_id).update(published_key=key)
    return key


message = publish.send(42, "Hello", "request-123")
```

`.send()` yields a message, not a result future by default. The Due producer
also acknowledges admission, but persists typed business intent with the owner.
If you configured Dramatiq Results, migrate those consumers explicitly; Due
will not satisfy `message.get_result(...)`.

An actor containing both the storage write and ORM update splits into two Due
methods. This is where the local stale-completion check becomes accessible.
Retaining the actor body unchanged behind a `.send()` facade would hide that
gain, even if the adapter could enqueue it successfully.

## After: model the obligation, split execution from completion

In your application's `models.py`, add a concrete work model. This example's
`OneToOneField` makes the document's **current publication** the obligation.
Use a distinct persisted request owner instead if every call must execute.
The fields below are from the [runnable models](https://github.com/gigaverse-app/due/blob/main/examples/documents/models.py);
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
producer. This is the [runnable effect](https://github.com/gigaverse-app/due/blob/main/examples/documents/effects.py),
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
| Actor `.send()` | Native admission function committing captured work with the product |
| Automatic actor retries | Due policy owns migrated retries; remove overlapping retry scheduling |
| Delayed send | Persist a fixed not-before time; use scheduled-action mode for changing deadlines |
| Middleware / CurrentMessage | Inventory the behavior actually used and inject it explicitly |
| Pipelines and groups | Keep their engine or model durable successors and aggregation |
| Result middleware | Persist the result consumers need in product/request state; no automatic result-backend substitution |
| Time limits | Order provider timeout, soft limit, hard limit and lease; none proves remote nonapplication |

## Pay particular attention to

Wrapping an actor's entire body fences only the wrapper's completion record.
The actor's own ORM writes and required callback messages remain outside that
protection. Split external work and local consequences before claiming the gain.

Preserve the retry classifier as well as the retry count. Authentication errors,
definite rejection and timeout-after-acceptance can require different outcomes.
A timeout is not a universally retryable failure for an external operation.

## Cutover and rollback

Deploy Due registration, worker routing and recovery alongside Dramatiq. Send
only new occurrences of the selected operation to Due, and let existing actor
messages, delays, retries and pipeline descendants complete on Dramatiq. Retain
actor names and old result lookup while those messages can arrive.

For a transferred cohort, stop its old retry authority and record unique source
message/occurrence mappings. Keep unresolved provider requests with their evidence.
Rollback affects new routing; Due keeps owning already-admitted work until
settled or explicitly transferred. Retire old middleware only when no remaining
actors need it.

## Can a thin adapter help?

A `.send()` facade for explicitly bound actors is feasible but not shipped.
Reject unsupported middleware/options instead of silently omitting them. Full
actor context, results and pipeline compatibility would be substantial work.
A useful facade delegates to a visible native capability; an unchanged actor
hidden behind it cannot expose the new settlement or uncertainty boundaries.

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

Prove middleware-supplied context survives, old/new retries do not overlap,
pipeline handoffs remain durable, and existing result consumers see the intended
business completion rather than mere delivery.

## Sources

[Actor guide](https://dramatiq.io/guide.html), [duplicate delivery and idempotency](https://dramatiq.io/best_practices.html).
