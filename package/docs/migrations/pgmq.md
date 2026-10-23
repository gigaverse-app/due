# Coming from PGMQ Python clients

PGMQ is a PostgreSQL message queue; its Python clients expose message operations.
Due is an application obligation runtime. Migrate a consumer's business operation
when you want shared completion and evidence handling, rather than expecting Due
to behave like a replacement PGMQ client.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
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

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


## The interfaces you will work with

| Familiar primitive | Its strength | Due counterpart and semantic change |
| --- | --- | --- |
| `send()` → message ID | PostgreSQL message persistence, including SQL clients | Application producer admits typed domain work |
| `read()` → message, visibility timeout | Consumer-controlled delivery and visibility | Registered Due runner claims work; no user read/ack loop |
| `archive()` / delete | Explicit acknowledgement and optional message retention | Fenced local settlement establishes completion; retention has a separate policy |

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

```sql
-- Producer (can share the product transaction on this connection):
SELECT pgmq.send(
    'publication',
    '{"document_id":42,"text":"Hello","publication_id":"request-123"}'::jsonb
);
-- Consumer claims one message for 60 seconds:
SELECT * FROM pgmq.read('publication', 60, 1);
-- Application validates message, writes the object, then updates the product.
-- Only after successful processing, use the msg_id returned by read:
SELECT pgmq.archive('publication', :msg_id);
```

`:msg_id` is a client-bound SQL parameter, not literal SQL to paste unchanged.
The application handler between `read` and `archive` owns the business operation:
`write_revision(text, publication_id)` followed by an ORM pointer update. Below,
those actions become `perform_effect` and `finalize_success`; Due owns dispatch
and completion, so this selected consumer no longer calls `read` or `archive`.

A visibility timeout makes an unacknowledged message available again. It does
not stop the previous consumer's storage write. Due's local fence prevents a
stale attempt from publishing the product pointer; the immutable-key storage
contract still makes repeat writes safe. PostgreSQL durability and transactional
send are existing PGMQ strengths, not gains introduced by this migration.

## After: model the obligation, split execution from completion

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


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
