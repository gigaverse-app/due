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

## Follow one operation from submission to completion

For document publication, the producer change looks like this. The first line
uses your existing application task; the second uses Due's runnable example:

```python
# Before (your existing task and configured source client):
task = await publish.kiq(document_id, text)

# After (with the Due example configured and the Document row already present):
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

The Taskiq handle and `wait_result()` contract need a deliberate replacement.
For publication, observe the canonical product output after settlement. If the
caller requires a result per invocation, persist a separate request identity and
result instead of returning a mutable document's latest value.

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
