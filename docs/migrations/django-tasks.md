# Coming from Django Tasks and the django-tasks backport

Keep the distinction between your task API and its execution backend visible.
Django Tasks defines how code submits and inspects work; the configured backend
provides execution. Due could become a backend, but it is not one today. You can
migrate selected operations now through Due's native API, or evaluate the bounded
backend design in this guide.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## Why consider Due?

For tasks that represent product obligations, Due can provide shared recovery,
fenced product completion and explicit provider uncertainty instead of leaving
those mechanisms inside each task. Atomic admission can close a gap when the
current backend is reached only after the product transaction commits. It adds
no new atomicity if your database backend already inserts work transactionally.

Keep your current backend when ordinary task execution and TaskResult are enough.
Due's richer semantics cost concrete work models, migrations and Celery/broker
operation. Its current Python/Django requirements also apply when the source API
is the backport. A supported frontend on an older Django version does not make
Due run on that version.

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
| `Task` / `@task`, `.enqueue()` | A standard task frontend independent of backend choice | A domain capability; Due does not yet implement `BaseTaskBackend` |
| `TaskResult`, `.refresh()`, `.return_value` | Typed task identity, status and result lookup | `PublicationWork` tracks obligation state; `Document` owns the business result |
| `.using(run_after=..., priority=...)` | Per-enqueue options where the backend supports them | `not_before` for new work; priority and arbitrary routing are not equivalent supported options |

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
from django.tasks import task
from .models import Document
from .storage import write_revision


@task
def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_revision(text, publication_id)
    Document.objects.filter(pk=document_id).update(published_key=key)
    return key


task_result = publish.enqueue(42, "Hello", "request-123")
# Later, once the backend reports SUCCESSFUL:
task_result.refresh()
key = task_result.return_value
```

The spelling change is from `publish.enqueue(...)` to an application producer
that commits the owner and work together. A `TaskResult` cannot be replaced with
a work row without updating status/result consumers. `task_result.refresh()`
and `.return_value` have no native Due equivalents returning the same contract.

The framework delegates execution guarantees to its backend. Evaluate the
backend you actually run: replacing an immediate development backend differs
from replacing transactional database admission. The concrete new boundary is
`finalize_success`, where the product update gains Due's revision/token check.

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

## Delayed delivery versus a movable deadline

With a timezone-aware `deadline`, the familiar delayed call is:

```python
publish.using(run_after=deadline).enqueue(42, "Hello", "request-123")
```

For a fixed initial delay, change the final line **inside the Due producer's
existing `atomic()` block** to:

```python
return pending.request_work(
    PublicationWork(text=text, universe="example"),
    not_before=deadline,
)
```

That sets when new work becomes eligible; delivery can happen later. Sending
unchanged inputs again is not a command to move an existing deadline. When the
product concept is “do this at a deadline that may change,” declare a separate
`ScheduledWork` model and `ScheduledAction` with `is_still_needed`,
`perform_effect` and `on_completed` hooks. Its policy requires `retention=None`;
explicit `input_fields` must include `run_at` and `cancelled`.
[Declaration example](../../tests/declarations/test_simple_callbacks.py).

For that registered capability, the native calls have meaningful result types:

```python
from due.django.capabilities.scheduled import CancellationResult, ScheduleResult

# deadline_action and DeadlineWork are your declared capability and work model.
status: ScheduleResult = deadline_action.schedule_at(
    42,
    DeadlineWork(universe="example"),
    at=deadline,
)
cancellation: CancellationResult = deadline_action.cancel_pending(42)
```

Handle `ALREADY_STARTED` from either operation: Due cannot promise prevention
once an attempt may have escaped. `UNCHANGED` from scheduling and
`NOT_SCHEDULED` from cancellation are distinct outcomes too. The useful gain is
an explicit, persisted deadline/cancellation contract; this does not reproduce
the source library's general revoke, cron or priority API.

## Migrate with the current Due API

Determine both the import (`django.tasks` versus `django_tasks`) and the installed
backend. Current backport versions separate database and RQ workers into
`django-tasks-db` and `django-tasks-rq`; older deployments may have bundled them.
Their storage/migration history cannot be replaced by changing one setting.
[Backport](https://github.com/RealOrangeOne/django-tasks),
[DB backend](https://github.com/RealOrangeOne/django-tasks-db).

**Available path today:** replace selected `.enqueue()` producers with explicit
Due admission and migrate their task bodies as described above. Keep other
backend aliases intact. The source function's return value, error list, attempts,
worker IDs and refreshable result are consumer contracts to preserve or explicitly
change. The example Due publication returns a work row, so it is not a drop-in
replacement for a caller expecting `.return_value`.

**Possible future path:** implement the [backend proposal below](#could-you-keep-the-standard-task-interface)
and retain `@task`/`.enqueue()` for its supported subset. No `TASKS` configuration
for Due is provided here because no such backend is implemented.

When migrating from an enqueue performed in `transaction.on_commit`, move durable
admission **inside** the product transaction. Leaving it in the callback still
permits the process to die after committing the product but before admitting
work. Conversely, do not claim to fix this gap for an existing database backend
that already inserts atomically on the same connection. For async request code,
run the product mutation and Due admission in one synchronous transactional
callable; an async enqueue wrapper alone does not join unrelated transactions.

## Cutover and rollback

Identify the configured backend, not just the decorator import. Deploy Due and
route only selected new operations to it; leave other tasks on their existing
backend aliases. Drain old work with the old worker and retain result lookup for
IDs clients already hold. A result ID needs its backend identity as well as its
string value during coexistence.

Do not reinterpret existing backend rows as Due work. Imported occurrences need
an explicit unique mapping, preserved arguments, provider keys and outcome
classification; unknown external calls stay with their evidence owner until a
reviewed transfer. Rollback changes new admissions while Due finishes admitted
work. Keep its registrations and recovery active for that cohort.

For backend-specific operations, the dedicated
[django-tasks-db](django-tasks-db.md) and
[django-tasks-rq](django-tasks-rq.md) guides include the corresponding cutover
inventory; the native migration above does not depend on an adapter shipping.

## Could you keep the standard task interface?

**The following is an unimplemented design**, included here so you can judge
whether it is a useful migration path. It is not an installation recipe.


Django's [backend contract](https://docs.djangoproject.com/en/6.0/ref/tasks/)
is the intended extension point. Preserve `Task`/`TaskResult` from Django rather
than inventing lookalikes. Check the installed minor version; the audited
[source](https://github.com/django/django/tree/52e5ba169694cbd70c538ce62275cd2abd7647fd/django/tasks)
and live docs can evolve independently.

### Admission and identity

Begin with an allowlist of replay-safe, synchronous module-level functions.
Record the function identity, normalized JSON arguments and an immutable
invocation identifier in a concrete invocation owner. Use one concrete Due work
model for that invocation family and one registered capability. Each enqueue
creates a new invocation, even when arguments match. Desired-state coalescing is
a separate native binding, not a hidden optimization.

Persist the invocation and work inside the caller's default Django transaction;
register the wake for after commit. Return an admission result without running
the function. A later rollback must remove both rows and the wake. Do not keep
only an in-memory callback until commit. A returned ID inside an uncommitted
transaction is provisional; after rollback lookup must report it missing.

These invocation/result models do not exist in Due today. They add migrations,
retention and API compatibility responsibilities even though the coordination
engine is reused. Do not create dynamic Django model classes per decorated
function at import time.

### Execution and results

Delegate claim, lease, execution, retry, recovery and finalization to Due. Store
the normalized return value in the same fenced transaction as successful
settlement. Store required task metadata where the corresponding transition is
owned; if Due lacks a public hook for required information, extend the shared
owner or exclude that feature rather than maintaining a second state machine.

The result model must account for errors, first start, last attempt, completion,
worker identities and attempt counts where the selected Django contract requires
them. Due's `attempt_count` and failure-code fields are not automatically an
accurate substitute for Django's result history. A crash between function return
and settlement can rerun a replay-safe function; this profile does not promise a
durable checkpoint of arbitrary return values.

Implement `enqueue`, `get_result`, their async surfaces, task validation and
correct Django exceptions. The standard base provides async wrappers, but that
does not imply arbitrary coroutine task execution or transaction propagation
across independently scheduled calls. Initially reject coroutine bodies,
non-default priority and unsupported options using validation/feature flags.
Reject `takes_context=True` until its attempt/result metadata is correctly
populated; later support it with Django's real `TaskContext`.

Use one supported queue initially. Due registers a work model once with a fixed
route; arbitrary per-invocation `.using(queue_name=...)` cannot simply override
that registration. Multiple queues require deliberately supported routing, not
ignored user options. Likewise, do not set `supports_priority=True` just because
a Celery broker accepts a priority argument: Due's recovery selection must
preserve the promised ordering too.

A later fixed-delay profile can map `run_after` to persisted `not_before` and
set `supports_defer` only once recovery respects it. This is distinct from
business cancellation and movable deadlines.

### Status projection

For a replay-safe invocation profile, a candidate projection is:

| Due state | Django task status | Required detail |
| --- | --- | --- |
| READY, QUEUED, RETRYABLE | READY | A retry remains unfinished; preserve attempt/error metadata |
| CLAIMED | RUNNING | Expose the actual lifecycle; don't infer liveness from an old result object |
| SUCCEEDED | SUCCESSFUL | Result was persisted with fenced completion |
| EXHAUSTED, TERMINAL_FAILED | FAILED | Preserve the stopped reason and result-access errors |
| UNKNOWN | **Not representable faithfully** | Standard API has no uncertainty state; exclude evidence-required tasks from this profile |

Do not translate UNKNOWN to FAILED and invite ordinary retries, or call it
RUNNING indefinitely while hiding the need for evidence. Evidence-required native
bindings need a separate obligation view/API that exposes uncertainty. A
Django TaskResult may describe an admission helper completing, but must then be
clearly distinguished from the underlying business operation completing.

### Native upgrade path

A future integration should allow registration-time binding from a frontend task
to an application admission function and capability, with explicit input and
result projection. **This is a design requirement, not a proposed shipped API
name.** It must expose the native work identity and supported capability to
operators and application code without requiring import from Due's private
runtime. Native functions can coexist beside standard tasks in the same app.

For existing invocation work, keep those occurrences under their old capability.
Move new admissions to the richer domain model only when identity and result
contracts are mapped. Switching the implementation of a task must not reinterpret
already-persisted payloads. Use versioned bindings and retain old handlers until
all their work and required result lookups drain.

A useful exit is: standard replay-safe task → explicit external/local split →
selected native projection/operation/schedule, while callers either retain a
truthful result contract or migrate to a richer one. If the adapter insists that
all obligations remain arbitrary functions with four task statuses, stop there;
it cannot expose Due's evidence model honestly.


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

For a backend implementation, additionally verify two equal enqueues create two
invocations, result refresh/error behavior matches the pinned Django version,
unsupported options fail before admission, and rollback removes provisional
result IDs. Exercise a native advanced capability through the integration: if
UNKNOWN or fenced business completion is inaccessible, the adapter has become a
limiting endpoint rather than a migration path.
