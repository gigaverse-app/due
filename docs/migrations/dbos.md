# Coming from DBOS

Due is a possible home for a bounded Django-owned operation, not a replacement
for DBOS's checkpointed workflow execution. The strongest migration candidate is
an operation whose code mostly manages provider evidence, changing desired state
and local product completion. Keep DBOS when durable orchestration is the value
you rely on.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## A useful gain has a narrow boundary

| Requirement | What Due can simplify | What you would lose by replacing the whole workflow |
| --- | --- | --- |
| Provider timeout with uncertain application | Explicit attempt/evidence and observation lifecycle | Existing steps and workflow recovery around that operation |
| Desired product state changes during execution | Revision-fenced local completion and declared projection repair | Historical workflow execution semantics if reduced to latest-state work |
| External success followed by local failure | Receipt-backed local settlement repair | Checkpointed results of other steps |
| Repeated custom obligation tests | Production-bound generated histories | Workflow replay/versioning tests still needed for retained DBOS code |

DBOS can implement reconciliation in workflow code too. Due's potential gain is
reusing a focused lifecycle with Django product rows. Transactional writes,
checkpointed steps and durable waits are already substantive DBOS capabilities.
A multi-step process often belongs there permanently.

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

## Work through one extraction

Suppose a DBOS workflow prepares a document, publishes it, waits for approval,
and sends a notification. Moving that entire function into `perform_effect`
would replay preparation and discard durable wait/checkpoint semantics.

Instead, identify the publication boundary. For **new**, Django-owned publication
requests, the native destination can be:

```python
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

The runnable example captures text, writes an immutable revision object and
publishes `Document.published_key` in fenced settlement. If this publication is
one event among many that must all happen, give each event its own persisted
owner; the example's one-row-per-document coalescing would change that contract.

A retained DBOS workflow must wait for the business completion of its stable
publication occurrence. Merely obtaining a Due work ID only establishes
admission. Make repeated calls across this boundary resolve the same occurrence;
neither the DBOS workflow ID nor a separate Django commit automatically does
that. Preserve the existing provider key for any occurrence that has already
started.

## Map the DBOS contract

Inventory workflows separately from steps, datasource transactions, queue
configuration, workflow IDs, handles/results, sleep, events/messages, children
and code-version recovery. Current documentation uses datasource-based
transactions; match the API to your installed DBOS version.
[Workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial),
[datasources](https://docs.dbos.dev/python/tutorials/transaction-tutorial),
[queues](https://docs.dbos.dev/python/tutorials/queue-tutorial).

Port only a bounded operation after deciding that its saved execution history is
not required. An idempotent external step can become `perform_effect`; its local
product update becomes fenced settlement. A non-repeatable step needs explicit
attempt evidence and observation. Keep the original provider idempotency key for
an occurrence that already crossed the boundary; Due's newly generated key is
not a safe substitute for it.

A workflow containing A, then a durable wait, then B must not become one retried
function. Either retain DBOS, or deliberately model phase state, wait conditions,
outputs and atomic successor admission using product rows and separate
capabilities. That is a workflow rewrite, not a thin shim. Due provides neither
DBOS history import nor checkpoint replay.

Drain started workflows on their original code/version and route only new
operations to Due. For coexistence, a DBOS workflow may reference one stable
Due-owned occurrence and observe its product result; arrange durable, idempotent
admission at the boundary. Do not assert that a DBOS transaction and a separate
Django transaction commit atomically. Existing workflow completion must wait for
the Due-owned result, not merely successful submission.

| DBOS concept | Due disposition |
| --- | --- |
| Workflow ID / duplicate start | Explicit unique product occurrence identity; do not use latest-state coalescing accidentally |
| External step | Replay-safe `perform_effect`, or operation/evidence mode after reviewing the provider contract |
| Datasource transaction | Move local completion into the supported Django transaction; no cross-connection atomicity |
| Persisted step result | Canonical product output or evidence; generic callbacks are not checkpoints |
| Sleep, messages, events, child workflow | Retain DBOS or deliberately redesign persisted phase state |
| Queue concurrency/rate limits | Preserve separately; Due per-work claims are not equivalent global flow control |
| Workflow code version/recovery | Keep original execution compatible until drained; no history importer ships |

## Transfer ownership safely

Prefer new operations going to Due while started DBOS workflows finish under
their original version. Record which engine owns each occurrence. Do not copy
workflow inputs into READY work without accounting for completed steps, in-flight
requests and retained outputs.

Rollback directs future operations to DBOS while Due finishes those it admitted.
If an existing workflow bridges to Due, keep that bridge until its outstanding
publication results are resolved; changing routing cannot erase that dependency.
Retain old workflow history for its operational and application consumers.

## Would a DBOS-shaped API be useful?

A bridge for a single operation can be useful. A shim for `@DBOS.workflow`, steps,
durable waits, transaction tracking and workflow handles would need another
workflow engine. It is not a thin adaptation layer, and no such API ships in Due.

Keep the native Due capability visible at the bridge so evidence and product
completion remain accessible. A design that reports success when submission
succeeds, or hides uncertainty behind an ordinary retry, defeats the migration.

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

For the bridge, crash after Due admission but before DBOS records its returned
ID: retry must find the same occurrence. Prove that workflow completion waits for
the external/product result, and that rollback cannot give both engines replay
authority. For any phase rewrite, exercise each saved output and wait boundary;
a passing single-effect contract is not workflow-equivalence evidence.
