# Coming from Chancy

Chancy already combines transactional PostgreSQL enqueueing with a substantial
worker and plugin ecosystem. Move a selected operation to Due when its difficult
part is product completion after uncertain external effects, rather than job
execution itself. A migration should retire a concrete application recovery
protocol, not merely exchange one queue for another.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due can make changing desired inputs, stale local completion and evidence-backed
settlement explicit in a shared capability. For example, a publication that was
written remotely but not committed locally can use retained evidence to repair
its product result. Projection and scheduled-action modes can replace bespoke
revision and deadline coordination where their policies fit.

Chancy already supplies transactional admission. It also offers queue controls,
multiple execution models and plugins for workflows, scheduling and observability.
Stay when those are the features you need, especially if PostgreSQL-only
operation matters. Due introduces Django, Celery and a broker; no performance or
infrastructure simplification is established by this guide.

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
| `@job`, immutable `Job.with_kwargs()` | Build explicit job descriptions before pushing | Concrete captured work fields plus capability declaration |
| `Chancy.push()` and returned `Reference` | PostgreSQL job persistence and lookup | Atomic owner/work admission; work identity is a business obligation |
| `QueuedJob`, executors and plugins | Execution context and configurable worker behavior | Due callbacks and policy; no executor/plugin compatibility layer |

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
from chancy import Chancy, Reference, job
from .models import Document
from .storage import write_revision


@job()
def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_revision(text, publication_id)
    Document.objects.filter(pk=document_id).update(published_key=key)
    return key


async def submit(chancy: Chancy) -> Reference:
    return await chancy.push(
        publish.job.with_kwargs(
            document_id=42,
            text="Hello",
            publication_id="request-123",
        )
    )
```

`publish.job.with_kwargs(...)` describes a function invocation; the new
`PublicationWork(text=...)` describes captured domain input. Chancy's `Reference`
does not become a result handle just because the Due row also has an ID.

Chancy already offers database-backed admission and configurable execution.
The potential gain is the split between a repeatable provider action and fenced
product publication. Preserve the semantics of any uniqueness, rate-limit,
retry or workflow plugins separately; a per-owner revision is not a plugin port.

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
| `@job` and `push` | Retain the reusable provider function; declare concrete work and a capability |
| Transactional push/cursor | Move both product and work onto the same supported Django connection |
| Job uniqueness | Choose a unique occurrence or desired-state revision; preserve the original rejection/coalescing behavior |
| Queue rate and concurrency limits | Keep explicit flow control; per-work claims are not global rate limits |
| Executor selection | Assess CPU, thread, process and coroutine needs against the actual Celery execution setup |
| Workflow plugin and dependencies | Retain Chancy workflow ownership or model durable phase outputs and handoffs |
| Cron, retry and recovery plugins | Separate business schedules from Due recovery and give each obligation one retry owner |
| Dashboard/result consumers | Replace required views with native product/work observability; no dashboard compatibility is implied |

## Pay particular attention to

A uniqueness key can mean “one queued job,” “one active job” or a business
occurrence identity. None should be inferred from Due's unchanged-input behavior.
Document when a second request is accepted, rejected or supersedes earlier work.

Do not claim that worker executor limits fence a remote provider. A process may
stop after the provider accepted a request. Select replay-safe or evidence-required
mode by that external contract, not by the executor that ran the function.

## Cutover and rollback

Install the Due work/evidence schema and run its recovery before routing a
selected producer. Keep Chancy workers/plugins for existing queued, running,
retrying and scheduled jobs, and keep workflow-owned children on Chancy until
that workflow finishes. Disable duplicate cron production for any migrated cohort.

If moving existing jobs, quiesce their Chancy execution/retry paths and preserve
unique identities and provider keys in a resumable mapping. Unknown requests
must not become READY. Roll back new admissions independently of existing Due
work; retain Due recovery until it owns no unresolved obligations.

## Can a thin adapter help?

A named `push` facade for a few audited jobs is feasible, but unimplemented.
Supporting arbitrary builders, cursor transactions, executor selection, rate
limits and plugins would require much more than translating a call. Reject
unmapped options before creating work.

The facade has value only if native settlement, projections and evidence remain
available. If it preserves only generic jobs, it offers little over Chancy's
existing transactional enqueue and can become an expensive compatibility layer.

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

Add checks for uniqueness lifetime, concurrency/rate limits, plugin callbacks,
workflow children, executor-dependent context and periodic occurrence identity.
Require an observed product benefit before accepting the added infrastructure.

## Sources

[Chancy manual](https://tkte.ch/chancy/), [source snapshot](https://github.com/TkTech/chancy/tree/b0e3e94fd12a567e47568d75f9d2d1d8fd996ea1).
