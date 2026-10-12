# Coming from Temporal

Treat this as a guide to moving a bounded product operation, not replacing a
Temporal application. Temporal's durable workflow history, replay, signals and
timers solve a broader orchestration problem. Due may simplify one Django-owned
effect inside that system while the workflow continues to own the process.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What a selective migration can improve

A provider operation may carry substantial application logic to correlate an
uncertain response, reject stale product completion and recover local settlement.
Due provides shared capabilities for those boundaries and can keep their truth
alongside Django product state. That can reduce the custom code inside an Activity.

Temporal can express reconciliation and compensation too. The benefit is reusable
Django-oriented obligation machinery, not a claim that Temporal cannot handle
these failures. Keep Temporal for multi-step, long-running or cross-service
orchestration. Replacing a Workflow with one retried callback would discard the
history and waiting semantics it depends on.

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
| `@workflow.defn`, Workflow execution/history | Durable control flow, timers, signals and replay | No whole-Workflow counterpart; retain orchestration |
| `@activity.defn`, Activity result/retry | External work with timeout and retry policies | A selected operation can become a native Due capability |
| `WorkflowHandle`, cancellation/queries | Interact with a durable execution | No handle compatibility; define an explicit domain observation/bridge contract |

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
from datetime import timedelta
from dataclasses import dataclass
from temporalio import activity, workflow
from temporalio.client import Client


@dataclass
class PublicationInput:
    document_id: int
    text: str
    publication_id: str


@activity.defn
async def publish_blob(data: PublicationInput) -> str:
    import asyncio
    from .models import Document
    from .storage import write_revision

    key = await asyncio.to_thread(write_revision, data.text, data.publication_id)
    await Document.objects.filter(pk=data.document_id).aupdate(published_key=key)
    return key


@workflow.defn
class PublishDocumentWorkflow:
    @workflow.run
    async def run(self, data: PublicationInput) -> str:
        return await workflow.execute_activity(
            publish_blob,
            data,
            start_to_close_timeout=timedelta(minutes=2),
        )


async def submit(client: Client) -> str:
    return await client.execute_workflow(
        PublishDocumentWorkflow.run,
        PublicationInput(42, "Hello", "request-123"),
        id="publication-request-123",
        task_queue="publication",
    )
```

`execute_workflow()` waits for the Workflow result; the Due producer below
returns when a database obligation has been admitted. Those calls are not
substitutable. A retained Workflow must observe actual completion through a
retry-safe Activity/coordination boundary, not execute Django I/O in Workflow code.

The example has one Activity. Replacing an entire Workflow containing timers,
signals or children would discard those semantics. Consider Due only for a
bounded Django-owned operation whose local product completion needs fencing.
Temporal already offers durable recovery; the gain is the domain settlement
boundary, not the mere existence of retries. Keep a distinct occurrence owner
when each Workflow execution owes a separate result.

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

## Map the boundaries

| Temporal concept | Due migration decision |
| --- | --- |
| Workflow identity/history | Retain on Temporal; no Due history importer or replay engine exists |
| Activity making the provider call | Extract a native effect/operation only with clear occurrence ownership |
| Activity retries and heartbeat timeout | One layer owns provider retries; bridge retries only find/observe the same occurrence |
| Durable timer, signal, query or child Workflow | Retain Temporal or intentionally redesign persisted phase state |
| Activity return value | Deliver the final canonical product result, not an admission ID presented as success |
| Workflow cancellation | Define whether it stops waiting, cancels pending work or owes compensation |
| Worker/code-version rollout | Keep old executions compatible until drained; a source edit does not convert history |

## Pay attention to cancellation and replay

A Workflow cancellation cannot prove a provider request did not apply. Once Due
owns an admitted operation, cancellation needs a product policy: pending work may
be canceled where supported, uncertain work still needs evidence, and cleanup
may require its own obligation. Do not delete the row merely because a caller
stopped waiting.

Temporal and Due cannot both independently retry the provider call. If a bridge
Activity times out, its next attempt should find the same Due occurrence and
observe progress. Preserve the provider key of an existing operation; a new
engine's fresh key may duplicate it.

## Cutover and rollback

Prefer new executions or a clearly versioned branch using the bridge while
existing workflows continue on their original path. Keep old Activity handlers,
workflow history and result consumers. Deploy Due migrations, registration and
recovery before any Workflow can submit to it.

Do not convert running workflows by copying only their inputs. Completed
Activities, outstanding timers, signals and in-flight calls all affect what is
still owed. Any full rewrite needs explicit phase-state and history mapping and
is outside a thin migration.

Rollback redirects new work. Existing workflows that reference Due must retain
the bridge and completion lookup until their obligations settle or transfer
explicitly. Keep Due evidence and recovery alive for those occurrences.

## Could Due expose the Temporal interface?

No thin SDK shim can preserve workflow/service history, deterministic replay,
durable timers and signals merely by forwarding task calls. No such integration
ships. A bounded Activity bridge can be useful if it leaves native evidence and
product completion visible; an API that treats enqueue success as Activity
completion hides the essential difference.

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

Also crash between Django admission and Activity acknowledgement, retry the
bridge, cancel while the provider outcome is unknown, and interrupt completion
notification. Require one occurrence and correct workflow progress in every
history. Passing Due's local contract alone does not establish Temporal replay
or bridge correctness.

## Sources

[Temporal workflow execution](https://docs.temporal.io/workflow-execution),
[Python SDK guide](https://docs.temporal.io/develop/python).
