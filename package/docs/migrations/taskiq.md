# Coming from Taskiq

Taskiq separates tasks, brokers, dependencies and result backends. Due can own
selected Django product obligations, but changing a Taskiq broker is not enough
to make the application use Due. Start with the effect that needs stronger
product coordination and explicitly retain the integrations it depends on.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
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

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


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
[shared implementation](../migrations.md#shared-native-implementation) shows the file-write body.

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

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


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
