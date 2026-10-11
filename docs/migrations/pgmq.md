# Coming from PGMQ Python clients

PGMQ is a PostgreSQL message queue; its Python clients expose message operations.
Due is an application obligation runtime. Migrate a consumer's business operation
when you want shared completion and evidence handling, rather than expecting Due
to behave like a replacement PGMQ client.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What Due can add

A PGMQ consumer often owns the decisions around a message: when to acknowledge,
how to recover a provider response, and whether a retry is safe. Due can put the
business obligation and local completion under a shared lifecycle, including
revision fencing and evidence-required uncertainty.

PostgreSQL persistence and transactional queue operations are already PGMQ
capabilities. They are not reasons by themselves to add Django, Celery and a
broker. Keep PGMQ for a language-neutral messaging substrate, mixed-language
consumers, or simple read/process/archive workflows whose contracts already fit.

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

## Move the business command, not the queue API

A producer might currently store a publication message through PGMQ SQL:

```sql
SELECT pgmq.send(
    'publication',
    '{"document_id": 42, "text": "Hello"}'::jsonb
);
```

For a new publication owned by the Django application, the Due destination is:

```python
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

The configured example commits product text and captured work together, writes
an immutable revision object, then publishes its key in fenced local settlement.
Validate the old message into canonical fields at one boundary. If each message
represents an independent event, use a distinct persisted request owner per
message/occurrence; the example's latest-publication coalescing is inappropriate.

The returned work ID is not a PGMQ message ID. The old read/visibility/archive
loop disappears only for the migrated business operation. Other queue consumers
continue to use PGMQ normally.

## Map message semantics deliberately

| PGMQ behavior | Due migration decision |
| --- | --- |
| Send payload in a transaction | Move product and work admission together to Due's supported Django connection |
| Read / visibility timeout | Due delivery reservations and work claims own execution; no API equivalence implied |
| Extend visibility | Configure/maintain the supported work ownership policy; do not manipulate PGMQ state for Due work |
| Delete/archive after processing | Fenced settlement establishes completion; evidence retention is a separate policy |
| Batch reads | Preserve per-message isolation and ordering requirements explicitly |
| Message ID and payload | Stable source-to-occurrence mapping and validated captured input |
| Other-language consumers | Retain PGMQ or provide an explicit service boundary; Due does not consume their wire protocol |

## Visibility is not evidence of nonapplication

When visibility expires, the original consumer may have already sent the provider
request. Another read must not be treated as proof that nothing happened. Due's
replay-safe mode still requires safe repetition; its evidence-required mode keeps
UNKNOWN until correlated observation establishes an outcome.

Similarly, archive means the queue consumer relinquished the message according
to its protocol. It is not automatically proof that every required product
consequence committed. Move those local consequences into settlement and admit
any required external successor durably in that transaction.

## Cutover and rollback

Deploy Due tables, registered workers and recovery. Route new commands for one
message type to native admission and let PGMQ consumers finish their existing
cohort. Inventory visible, invisible and archived-but-unsettled business work;
queue depth alone cannot establish that no obligation remains.

If transferring messages, stop their old producers/consumers and retry paths,
account for in-flight provider calls, and retain a unique message-to-occurrence
mapping with original provider keys. Never acknowledge the source merely because
an in-memory plan to admit Due work exists. A reviewed transfer must establish
durable destination admission and prevent old execution before granting new
execution authority. There is no generic importer.

Rollback redirects new commands while Due retains already-admitted work.
Keep PGMQ archive/history access and Due evidence as required. Never let both a
visibility-timeout redelivery and Due recovery execute the same transferred effect.

## Is a PGMQ client shim worthwhile?

A named business-command facade can be small. Emulating read, visibility, delete,
archive and batch APIs would hide Due's lifecycle behind a different protocol
and require compatibility with other consumers. No such shim ships, and it is
not the recommended migration path.

Keep native operation/evidence and settlement accessible. If callers must still
acknowledge messages to define completion, the adapter has retained a second
completion authority rather than simplifying the application.

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

Exercise visibility expiry while the first provider call is still in flight,
a crash before/after source acknowledgement, batch poison isolation, duplicate
message transfer and mixed-language consumers. Confirm the product result and
one execution owner for every transferred occurrence.

## Sources

[PGMQ SQL functions](https://pgmq.github.io/pgmq/api/sql/functions/),
[Python client](https://pgmq.github.io/pgmq-py/1.1.1/),
[reading and visibility](https://pgmq.github.io/pgmq-py/1.1.1/reading_messages/).
