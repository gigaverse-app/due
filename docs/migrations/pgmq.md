# Coming from PGMQ Python clients

PGMQ is a PostgreSQL message queue; its Python clients expose message operations.
Due is an application obligation runtime. Migrate a consumer's business operation
when you want shared completion and evidence handling, rather than expecting Due
to behave like a replacement PGMQ client.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What Due can add

A PGMQ consumer often owns the decisions around a message: when to acknowledge,
how to recover a provider response, and whether a retry is safe. Due can put the
business obligation and local completion under a shared lifecycle, including
revision fencing and evidence-required uncertainty.

PostgreSQL persistence and transactional queue operations are already PGMQ
capabilities. They are not reasons by themselves to add Django, Celery and a
broker. Keep PGMQ for a language-neutral messaging substrate, mixed-language
consumers, or simple read/process/archive workflows whose contracts already fit.

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
| `send()` → message ID | PostgreSQL message persistence, including SQL clients | Application producer admits typed domain work |
| `read()` → message, visibility timeout | Consumer-controlled delivery and visibility | Registered Due runner claims work; no user read/ack loop |
| `archive()` / delete | Explicit acknowledgement and optional message retention | Fenced local settlement establishes completion; retention has a separate policy |

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

```sql
-- Producer (can share the product transaction on this connection):
SELECT pgmq.send(
    'publication',
    '{"document_id":42,"text":"Hello","publication_id":"request-123"}'::jsonb
);
-- Consumer claims one message for 60 seconds:
SELECT * FROM pgmq.read('publication', 60, 1);
-- Application validates message, writes the object, then updates the product.
-- Only after successful processing, use the msg_id returned by read:
SELECT pgmq.archive('publication', :msg_id);
```

`:msg_id` is a client-bound SQL parameter, not literal SQL to paste unchanged.
The application handler between `read` and `archive` owns the business operation:
`write_revision(text, publication_id)` followed by an ORM pointer update. Below,
those actions become `perform_effect` and `finalize_success`; Due owns dispatch
and completion, so this selected consumer no longer calls `read` or `archive`.

A visibility timeout makes an unacknowledged message available again. It does
not stop the previous consumer's storage write. Due's local fence prevents a
stale attempt from publishing the product pointer; the immutable-key storage
contract still makes repeat writes safe. PostgreSQL durability and transactional
send are existing PGMQ strengths, not gains introduced by this migration.

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

## Map message semantics deliberately

| PGMQ behavior | Due migration decision |
| --- | --- |
| Send payload in a transaction | Move product and work admission together to Due's supported Django connection |
| Read / visibility timeout | Due delivery reservations and work claims own execution; no API equivalence implied |
| Extend visibility | Configure/maintain the supported work ownership policy; do not manipulate PGMQ state for Due work |
| Delete/archive after processing | Fenced settlement establishes completion; evidence retention is a separate policy |
| Batch reads | Preserve per-message isolation and ordering requirements explicitly |
| Message ID and payload | Stable source-to-occurrence mapping and validated captured input |
| Other-language consumers | Retain PGMQ or provide an explicit service boundary; Due does not consume their wire protocol |

## Visibility is not evidence of nonapplication

When visibility expires, the original consumer may have already sent the provider
request. Another read must not be treated as proof that nothing happened. Due's
replay-safe mode still requires safe repetition; its evidence-required mode keeps
UNKNOWN until correlated observation establishes an outcome.

Similarly, archive means the queue consumer relinquished the message according
to its protocol. It is not automatically proof that every required product
consequence committed. Move those local consequences into settlement and admit
any required external successor durably in that transaction.

## Cutover and rollback

Deploy Due tables, registered workers and recovery. Route new commands for one
message type to native admission and let PGMQ consumers finish their existing
cohort. Inventory visible, invisible and archived-but-unsettled business work;
queue depth alone cannot establish that no obligation remains.

If transferring messages, stop their old producers/consumers and retry paths,
account for in-flight provider calls, and retain a unique message-to-occurrence
mapping with original provider keys. Never acknowledge the source merely because
an in-memory plan to admit Due work exists. A reviewed transfer must establish
durable destination admission and prevent old execution before granting new
execution authority. There is no generic importer.

Rollback redirects new commands while Due retains already-admitted work.
Keep PGMQ archive/history access and Due evidence as required. Never let both a
visibility-timeout redelivery and Due recovery execute the same transferred effect.

## Is a PGMQ client shim worthwhile?

A named business-command facade can be small. Emulating read, visibility, delete,
archive and batch APIs would hide Due's lifecycle behind a different protocol
and require compatibility with other consumers. No such shim ships, and it is
not the recommended migration path.

Keep native operation/evidence and settlement accessible. If callers must still
acknowledge messages to define completion, the adapter has retained a second
completion authority rather than simplifying the application.

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

Exercise visibility expiry while the first provider call is still in flight,
a crash before/after source acknowledgement, batch poison isolation, duplicate
message transfer and mixed-language consumers. Confirm the product result and
one execution owner for every transferred occurrence.

## Sources

[PGMQ SQL functions](https://pgmq.github.io/pgmq/api/sql/functions/),
[Python client](https://pgmq.github.io/pgmq-py/1.1.1/),
[reading and visibility](https://pgmq.github.io/pgmq-py/1.1.1/reading_messages/).
