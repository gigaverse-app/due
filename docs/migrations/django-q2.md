# Coming from Django Q2

Django Q2 already integrates workers, schedules, results and administration with
Django. Consider Due when selected tasks have accumulated custom obligation
recovery or provider reconciliation. The goal is to simplify those operations,
not to replace a satisfactory Django task system wholesale.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due provides a shared lifecycle for captured inputs, stale-completion rejection,
uncertain effects and evidence-backed local repair. Atomic admission can improve
a product-to-broker boundary when it currently spans separate commits. The gain
depends on the actual Q2 broker configuration; do not claim a gap for an ORM
admission path already participating in the same transaction.

Stay with Q2 when its schedules, hooks, chains, broker choices and admin views
satisfy the workload. Due adds Celery, its broker and concrete work tables, and
does not preserve those Q2 surfaces automatically.

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
| `async_task()` and import-path functions | Lightweight Django dispatch with broker choices | Explicit typed capability and producer |
| `Task`, `result(task_id)`, hooks | Persisted results and completion hooks | Work state plus `finalize_success` in fenced local settlement |
| `q_options`, schedules, chains/groups | Per-task execution options and orchestration | Policy and domain-owned successors; no chain/schedule importer |

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
from django_q.tasks import async_task, result
from .models import Document
from .storage import write_revision

def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_revision(text, publication_id)
    Document.objects.filter(pk=document_id).update(published_key=key)
    return key

task_id = async_task(publish, 42, "Hello", "request-123")
# Later; None can mean not ready, so also inspect task status.
key = result(task_id)
```

A Q2 hook receives the completed task, including its result. The comparable
Due completion method receives captured work and the provider result, and runs
under the validated owner/work fence. Move only local DB consequences into it;
external follow-ups need their own admitted obligation.

Do not translate `timeout` and broker `retry` into identical Due numbers. They
control different retry/visibility boundaries. The gain is a persistent product
obligation and explicit local settlement; the atomic-admission comparison depends
on whether your current Q2 broker already uses the same database transaction.

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
| `async_task` / `AsyncTask` | Explicit producer and concrete captured work |
| `q_options` | Inventory routing, timeout, hooks and result options; reject unmapped values |
| Broker selection, including ORM | Verify old transaction/delivery semantics; Due uses its documented Django/Celery path |
| Retry, timeout and acknowledgement | One retry authority with a declared replay/evidence policy |
| DB or cached results | Preserve lookup lifetime and required output independently of work retention |
| Hooks / chains / groups | Local consequences in settlement; external successors as durable obligations |
| Scheduled and missed occurrences | Define catch-up and occurrence identity; recovery cadence is not business cron |

## Pay particular attention to

Q2 broker choice affects what a message acknowledgement means. Audit the actual
configuration rather than treating all Q2 deployments as Redis queues. Due
cannot retroactively repair a lost pre-cutover obligation unless durable product
facts still identify it.

A hook that sends the next task must become a durable handoff if the next effect
is required for product completion. Marking the parent successful before that
handoff commits would preserve the very gap the migration is meant to remove.

## Cutover and rollback

Run Due migrations, registrations, workers and recovery before moving a selected
producer. Let Q2 complete its old broker entries and chains; retain DB/cache
results for old task IDs. Inventory running, queued, failed and scheduled work,
including missed schedules whose catch-up policy still owes occurrences.

Disable only the migrated schedule producer, and only once the new owner is live.
Imports require a quiesced execution/retry path and stable occurrence/provider
mapping. Rollback redirects new requests; Due keeps its admitted obligations,
evidence and recovery until resolved. Keep Q2 admin access while old work remains.

## Can a thin adapter help?

A bound `async_task` facade may be practical, but no adapter ships. It cannot
promise all `q_options`, broker modes, hooks, cached results and schedules from
the function signature alone. Keep native capability access and reject unsupported
options early. Reproducing Q2's whole worker/admin/results system would make the
facade a second platform rather than a small migration aid.

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

Check the selected broker's transaction boundary, chain/hook failure, cached
result expiry, timeout/retry interaction, missed-schedule catch-up and old-ID
lookup. Prove one execution owner per accepted occurrence during rollback.

## Sources

[Django Q2 documentation](https://django-q2.readthedocs.io/en/master/), [task API](https://django-q2.readthedocs.io/en/master/tasks.html).
