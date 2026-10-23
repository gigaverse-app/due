# Coming to Due from another library

Choose the library you use today. Each guide puts the migration decision,
source-specific code, semantic changes, cutover and rollback together. The shared
setup and native Due implementation below are maintained once for all guides.

| Coming from | What the guide helps you preserve |
| --- | --- |
| [Procrastinate](migrations/procrastinate.md) | Transactional deferral, execution/queueing locks and stalled jobs |
| [DBOS](migrations/dbos.md) | Bounded operations, checkpointed workflows and durable waits |
| [RQ](migrations/rq.md) | Job identities, results, dependencies and Redis cutover |
| [Django Tasks / django-tasks](migrations/django-tasks.md) | Native migration today and the proposed standard backend |
| [django-tasks-db](migrations/django-tasks-db.md) | ORM task rows, db_worker and result retention |
| [django-tasks-rq](migrations/django-tasks-rq.md) | TaskResult plus RQ delivery and backend-aware lookup |
| [django-rq](migrations/django-rq.md) | Django producer paths, queue settings and admin continuity |
| [Celery](migrations/celery.md) | Keep the workers; replace custom obligation recovery |
| [Chancy](migrations/chancy.md) | Transactional push, uniqueness, plugins and executors |
| [PgQueuer](migrations/pgqueuer.md) | Connection-bound admission, async handlers and serialized dispatch |
| [Dramatiq](migrations/dramatiq.md) | Actors, middleware, retries and successor handoffs |
| [Huey](migrations/huey.md) | Task calls, results, locks, revoke and scheduling |
| [arq](migrations/arq.md) | Async context, job IDs, expiry and cancellation |
| [Taskiq](migrations/taskiq.md) | Broker plugins, dependency injection, labels and results |
| [Django Q2](migrations/django-q2.md) | Broker choices, q_options, hooks and missed schedules |
| [Temporal](migrations/temporal.md) | Activity boundaries, workflow ownership and cancellation |
| [PGMQ Python clients](migrations/pgmq.md) | Visibility, acknowledgement, archive and message transfer |

## Read the decision before changing the code

Due is useful when an application must remember a business obligation beyond
one execution attempt. The guides identify concrete benefits—atomic product/work
admission, fenced local completion, explicit provider uncertainty and reusable
recovery—without assuming these are absent from your current system.

They also explain when to stay. PostgreSQL queues already provide durable,
transactional enqueue; workflow engines provide execution history and orchestration
that Due does not replace. For the broader picture, read
[Due and the Python background-work ecosystem](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md).

The current package is alpha, requires Python 3.14+, Django >=6.0.8,<7,
PostgreSQL and Celery with a broker and recurring recovery. No compatibility
adapter described in these guides is implemented. Each guide distinguishes
migration through today's native API from possible future integration.

The native target is grounded in the existing [runnable document example](https://github.com/gigaverse-app/due/blob/main/examples/documents).
Its source remains the canonical implementation; the guides explain how each
source library's semantics map to it and where the domain needs a different model.
Each guide shows the source task and caller. The shared recipe below owns the
concrete Due models, effect/completion split and atomic producer. Source examples assume a
configured client/backend and importable application modules. The native excerpts
come from the runnable example; the side-by-side comparisons are not claims of
executed cross-library parity.

Reviewed on **2026-10-11** against Due `65e0bcf`. The
[source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register) records the inspected upstream
snapshots. These guides make no comparative performance or production-readiness
claim; validate the chosen path against your installed versions and real workload.

## Prepare the Due side

Install `django-due` in a Python 3.14+ application using Django 6.0.8+ (below 7),
PostgreSQL, Celery and a broker. Pin the version in your lockfile. Start with the
[runnable recovery walkthrough](../../docs/recovery-walkthrough.md) before changing
an existing application. Product, work and evidence use one writable database.

1. Add `due.django` to `INSTALLED_APPS` and apply its migrations. Existing tables
   need the [migration precautions](integration.md#migrating-an-existing-application).
2. Define the owner and concrete work model, then apply your app's migration.
3. Split the external operation from local completion. Choose the
   [capability](capabilities.md) matching the provider's replay/evidence contract.
4. [Register the capability](integration.md) in each web, worker, Beat and command
   process. Run workers for the registered queue and a recurring scheduler.
5. Bind [conformance checks](conformance.md) to your own product outcome. A passing
   document example does not validate your provider or application callbacks.

## Shared native implementation

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
from due.django.public import DurableWorkPolicy, DurableWorkState, ReplaySafeEffect
from .models import Document, PublicationWork
from .storage import write_revision


class PublishDocument(ReplaySafeEffect[Document, PublicationWork, str]):
    def perform_effect(self, work: PublicationWork) -> str:
        # Each revision writes its own immutable key. A late old worker cannot
        # overwrite the current revision's bytes. Retrying this key is safe.
        return write_revision(work.text, work.provider_idempotency_key())

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

Use the [example storage implementation](../../examples/documents/storage.py)
for `write_revision`, or bind your own replay-safe provider. The filesystem
stands in for replay-safe object storage. For multiple worker
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
per submission. Apply the schema migration and registration in the [integration guide](integration.md)
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
