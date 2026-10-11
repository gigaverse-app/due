# Coming from PgQueuer

PgQueuer is a natural choice for PostgreSQL-backed async jobs with transactional
enqueue. Due becomes interesting when a handler also needs a durable business
lifecycle: changing desired inputs, provider uncertainty, or recoverable local
completion. Preserve PgQueuer when its existing execution model fits better.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
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
job_ids = await queries.enqueue("publish", publication_payload)

# After (with the Due example configured and the Document row already present):
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

Decode the existing byte payload into explicit, validated domain input at one
boundary. The Due example takes `document_id` and `text`; it does not accept an
opaque PgQueuer Job. Preserve payload version and tenant scope rather than
parsing differently in each worker. The old return and new work ID are not the
same completion-watcher contract.

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
