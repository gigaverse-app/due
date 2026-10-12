# Coming from DBOS

Due is a possible home for a bounded Django-owned operation, not a replacement
for DBOS's checkpointed workflow execution. The strongest migration candidate is
an operation whose code mostly manages provider evidence, changing desired state
and local product completion. Keep DBOS when durable orchestration is the value
you rely on.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## A useful gain has a narrow boundary

| Requirement | What Due can simplify | What you would lose by replacing the whole workflow |
| --- | --- | --- |
| Provider timeout with uncertain application | Explicit attempt/evidence and observation lifecycle | Existing steps and workflow recovery around that operation |
| Desired product state changes during execution | Revision-fenced local completion and declared projection repair | Historical workflow execution semantics if reduced to latest-state work |
| External success followed by local failure | Receipt-backed local settlement repair | Checkpointed results of other steps |
| Repeated custom obligation tests | Production-bound generated histories | Workflow replay/versioning tests still needed for retained DBOS code |

DBOS can implement reconciliation in workflow code too. Due's potential gain is
reusing a focused lifecycle with Django product rows. Transactional writes,
checkpointed steps and durable waits are already substantive DBOS capabilities.
A multi-step process often belongs there permanently.

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
| `@DBOS.workflow()` | Durable orchestration with checkpoint replay | No whole-workflow counterpart; select one bounded operation |
| `@DBOS.step()` and saved output | Skip completed steps during workflow recovery | `perform_effect` may repeat; replay-safe contract or evidence mode required |
| Queue, workflow handle, `.get_result()` | Queued durable execution and a workflow result | Admission returns work; business completion lives in the product row |

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
from dbos import DBOS
from .models import Document
from .storage import write_revision


@DBOS.step()
def write_blob(text: str, publication_id: str) -> str:
    return write_revision(text, publication_id)


@DBOS.step()
def publish_pointer(document_id: int, key: str) -> None:
    Document.objects.filter(pk=document_id).update(published_key=key)


@DBOS.workflow()
def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_blob(text, publication_id)
    publish_pointer(document_id, key)
    return key


# After DBOS configuration and launch:
DBOS.register_queue("publication")
handle = DBOS.enqueue_workflow("publication", publish, 42, "Hello", "request-123")
key = handle.get_result()
```

After a checkpointed `write_blob` step, DBOS can recover the workflow using
its saved key. A replay-safe Due effect can call storage again after a crash:
its contract is safe repetition, not checkpoint replay. In exchange, the local
pointer update below is validated against the current owner/work revision.
Both properties matter; one is not a stronger spelling of the other.

This simple workflow has no durable waits or children. Extracting it is plausible
when the application owes only the latest document publication. If it instead
owes every workflow occurrence, persist a separate request owner per occurrence.
Keep DBOS for multi-step history, sleeps, events and orchestration. A DBOS-to-Due
bridge must retry admission with stable occurrence identity and wait for actual
completion; a work ID is not a replacement workflow result.

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

## Map the DBOS contract

Inventory workflows separately from steps, datasource transactions, queue
configuration, workflow IDs, handles/results, sleep, events/messages, children
and code-version recovery. Current documentation uses datasource-based
transactions; match the API to your installed DBOS version.
[Workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial),
[datasources](https://docs.dbos.dev/python/tutorials/transaction-tutorial),
[queues](https://docs.dbos.dev/python/tutorials/queue-tutorial).

Port only a bounded operation after deciding that its saved execution history is
not required. An idempotent external step can become `perform_effect`; its local
product update becomes fenced settlement. A non-repeatable step needs explicit
attempt evidence and observation. Keep the original provider idempotency key for
an occurrence that already crossed the boundary; Due's newly generated key is
not a safe substitute for it.

A workflow containing A, then a durable wait, then B must not become one retried
function. Either retain DBOS, or deliberately model phase state, wait conditions,
outputs and atomic successor admission using product rows and separate
capabilities. That is a workflow rewrite, not a thin shim. Due provides neither
DBOS history import nor checkpoint replay.

Drain started workflows on their original code/version and route only new
operations to Due. For coexistence, a DBOS workflow may reference one stable
Due-owned occurrence and observe its product result; arrange durable, idempotent
admission at the boundary. Do not assert that a DBOS transaction and a separate
Django transaction commit atomically. Existing workflow completion must wait for
the Due-owned result, not merely successful submission.

| DBOS concept | Due disposition |
| --- | --- |
| Workflow ID / duplicate start | Explicit unique product occurrence identity; do not use latest-state coalescing accidentally |
| External step | Replay-safe `perform_effect`, or operation/evidence mode after reviewing the provider contract |
| Datasource transaction | Move local completion into the supported Django transaction; no cross-connection atomicity |
| Persisted step result | Canonical product output or evidence; generic callbacks are not checkpoints |
| Sleep, messages, events, child workflow | Retain DBOS or deliberately redesign persisted phase state |
| Queue concurrency/rate limits | Preserve separately; Due per-work claims are not equivalent global flow control |
| Workflow code version/recovery | Keep original execution compatible until drained; no history importer ships |

## Transfer ownership safely

Prefer new operations going to Due while started DBOS workflows finish under
their original version. Record which engine owns each occurrence. Do not copy
workflow inputs into READY work without accounting for completed steps, in-flight
requests and retained outputs.

Rollback directs future operations to DBOS while Due finishes those it admitted.
If an existing workflow bridges to Due, keep that bridge until its outstanding
publication results are resolved; changing routing cannot erase that dependency.
Retain old workflow history for its operational and application consumers.

## Would a DBOS-shaped API be useful?

A bridge for a single operation can be useful. A shim for `@DBOS.workflow`, steps,
durable waits, transaction tracking and workflow handles would need another
workflow engine. It is not a thin adaptation layer, and no such API ships in Due.

Keep the native Due capability visible at the bridge so evidence and product
completion remain accessible. A design that reports success when submission
succeeds, or hides uncertainty behind an ordinary retry, defeats the migration.

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

For the bridge, crash after Due admission but before DBOS records its returned
ID: retry must find the same occurrence. Prove that workflow completion waits for
the external/product result, and that rollback cannot give both engines replay
authority. For any phase rewrite, exercise each saved output and wait boundary;
a passing single-effect contract is not workflow-equivalence evidence.
