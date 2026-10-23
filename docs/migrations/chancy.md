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

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


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
[shared implementation](../migrations.md#shared-native-implementation) shows the file-write body.

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

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


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
