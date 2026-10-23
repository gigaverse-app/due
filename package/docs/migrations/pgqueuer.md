# Coming from PgQueuer

PgQueuer is a natural choice for PostgreSQL-backed async jobs with transactional
enqueue. Due becomes interesting when a handler also needs a durable business
lifecycle: changing desired inputs, provider uncertainty, or recoverable local
completion. Preserve PgQueuer when its existing execution model fits better.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
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

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


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
[shared implementation](../migrations.md#shared-native-implementation) shows the file-write body.

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

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


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
