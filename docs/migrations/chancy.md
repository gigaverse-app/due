# Coming from Chancy

Chancy already combines transactional PostgreSQL enqueueing with a substantial
worker and plugin ecosystem. Move a selected operation to Due when its difficult
part is product completion after uncertain external effects, rather than job
execution itself. A migration should retire a concrete application recovery
protocol, not merely exchange one queue for another.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
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
[document example](../../examples/documents/effects.py), its
[models](../../examples/documents/models.py) and
[registration](../../examples/documents/apps.py) form a complete implementation;
reuse that structure instead of inventing a queue wrapper. The `examples` package
belongs to this repository, not the installed Due runtime. In your application,
implement the corresponding producer and models in your own app.

## Follow one operation from submission to completion

For document publication, the producer change looks like this. The first line
uses your existing application task; the second uses Due's runnable example:

```python
# Before (your existing task and configured source client):
job = await chancy.push(publish.job.with_kwargs(document_id=document_id, text=text))

# After (with the Due example configured and the Document row already present):
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

The Chancy job builder's options need a separate disposition. A Due producer
captures domain inputs and uses a registered route; it is not an executor choice
or queue declaration. In async code, run the product write and Due admission in
one synchronous transactional boundary; do not block the event loop by blindly
replacing `await chancy.push(...)` with a synchronous ORM call.

The destination commits the new document text and its obligation together.
`perform_effect` writes a revision-specific immutable object;
`finalize_success` publishes its key under Due's ownership/revision check.
Putting the old function wholesale in `perform_effect` would leave its embedded
DB updates outside that check. Completion callbacks do local DB work only;
required external follow-ups become separately admitted obligations.

The example owes the **current publication**: equal input preserves the existing
outcome, while changed input starts a revision. If every submission must execute,
create a distinct persisted request owner per occurrence instead. `work.pk` is
an obligation identifier, not a source-library result handle.

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
