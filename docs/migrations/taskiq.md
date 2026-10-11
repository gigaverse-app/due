# Coming from Taskiq

Taskiq separates tasks, brokers, dependencies and result backends. Due can own
selected Django product obligations, but changing a Taskiq broker is not enough
to make the application use Due. Start with the effect that needs stronger
product coordination and explicitly retain the integrations it depends on.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due can make product/work admission atomic on Django's supported connection,
recover lost wakeups and fence product completion after revisions change. Its
operation modes make provider uncertainty observable, and its conformance
contracts can exercise your actual application boundaries.

Stay with Taskiq when async task execution, dependency injection and broker/plugin
choice are the value you need. Those are not provided by a Due producer facade.
No universal durability claim is made about Taskiq: delivery and results depend
on the selected components, which must be audited in the installed application.

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
| `AsyncTaskiqDecoratedTask`, `.kiq()` | Typed async submission via interchangeable brokers | Typed capability and a synchronous transaction boundary |
| `AsyncTaskiqTask`, `TaskiqResult` | Result-backend lookup and return/error information | Work state plus a canonical product result |
| Dependency injection, labels, middleware | Worker resource management and pluggable policy | Explicit effect dependencies/registration; no automatic label or DI port |

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
from .broker import broker
from .models import Document
from .storage import write_revision

@broker.task
async def publish(document_id: int, text: str, publication_id: str) -> str:
    key = await asyncio.to_thread(write_revision, text, publication_id)
    await Document.objects.filter(pk=document_id).aupdate(published_key=key)
    return key

async def submit() -> str:
    task = await publish.kiq(42, "Hello", "request-123")
    # Requires a configured result backend.
    result = await task.wait_result(timeout=30)
    assert not result.is_err, "Inspect the task error before using its result"
    assert isinstance(result.return_value, str)
    return result.return_value
```

The returned Taskiq task/result wrappers are not preserved by changing the
producer. The native Due version below returns work immediately and stores its
settled key in the product row. Result-backend consumers need a new domain API.

A Taskiq-injected provider client needs an explicit owner/lifetime in the new
effect. If it is async, `run_effect_coroutine(async_helper, ...)` is the native
bridge, not automatic reuse of Taskiq's dependency context. Gains in fenced
completion and recovery must justify leaving broker and middleware flexibility;
labels do not automatically become Due scheduling or priority controls.

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
| `.kiq()` / kicker options | Native admission for an allowlisted operation; reject unported labels/options |
| Broker middleware | Inventory and retain serialization, context, tracing and delivery behavior actually required |
| Dependency injection | Inject the provider/application collaborators explicitly into the capability |
| Result backend / `wait_result` | Canonical result projection or migrate callers; no automatic backend substitution |
| Task labels and timeouts | Translate supported controls deliberately; local timeout is not remote cancellation |
| Scheduling source | Keep business schedules or model durable occurrence creation; Due recovery is separate |
| Async request/function | One supported transactional admission boundary; separately assess coroutine execution and clients |

## Pay particular attention to

A task label may be interpreted by middleware or a broker plugin. Silently
accepting it while doing nothing is a behavior change. Write down which component
owns each important option before translating it.

Do not assume a global claim/order guarantee from a queue name. Due's ownership
is per work row, while a Taskiq deployment's concurrency and routing are properties
of its configured components. Preserve a required rate or serialization rule
explicitly, or leave that operation on its current path.

## Cutover and rollback

Deploy Due schema, registration, workers and recovery alongside the existing
Taskiq deployment. Route only new occurrences of a selected task; retain broker
consumers, middleware and result lookup for previously submitted IDs. Keep one
scheduler producing each business occurrence.

Any transfer needs a unique source-task mapping and a quiesced old retry/delivery
path. Retain unknown provider requests and their keys. Rollback redirects future
admission, while Due completes what it already accepted. Do not delete a result
backend just because the new producer no longer uses it.

## Can a thin adapter help?

A `.kiq()` facade for explicitly bound producers could reduce source edits;
none ships. It cannot imply compatibility with every broker, dependency provider,
label or result backend. That wider parity is unassessed here.

Keep access to native work, evidence and fenced completion. If supporting the
old interface requires rebuilding Taskiq's injection/middleware runtime, the
adapter has outgrown a useful thin migration layer.

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

Test the actual installed broker/result plugins, required labels, injected
resources, async admission rollback, result waiting and scheduler ownership.
Generic task tests do not establish plugin compatibility.

## Sources

[Taskiq getting started](https://taskiq-python.github.io/guide/getting-started.html), [official documentation](https://taskiq-python.github.io/).
