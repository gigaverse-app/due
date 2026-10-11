# Coming from Dramatiq

Start with an actor whose retries are surrounded by custom product recovery
logic. Due can centralize that lifecycle while retaining the actor's useful
provider helper. Ordinary actors with well-understood idempotency, middleware
and result behavior can remain on Dramatiq.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
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
message = publish.send(document_id, text)

# After (with the Due example configured and the Document row already present):
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

A message ID identifies the old submission; the new row represents what the
product owes. Move required local writes from the actor body into fenced
settlement. If middleware supplied tenant context or clients, install equivalent
explicit startup/context wiring before running the extracted helper.

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
