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

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


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
[shared implementation](../migrations.md#shared-native-implementation) shows the file-write body.

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

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


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
