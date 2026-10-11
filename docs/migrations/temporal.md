# Coming from Temporal

Treat this as a guide to moving a bounded product operation, not replacing a
Temporal application. Temporal's durable workflow history, replay, signals and
timers solve a broader orchestration problem. Due may simplify one Django-owned
effect inside that system while the workflow continues to own the process.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What a selective migration can improve

A provider operation may carry substantial application logic to correlate an
uncertain response, reject stale product completion and recover local settlement.
Due provides shared capabilities for those boundaries and can keep their truth
alongside Django product state. That can reduce the custom code inside an Activity.

Temporal can express reconciliation and compensation too. The benefit is reusable
Django-oriented obligation machinery, not a claim that Temporal cannot handle
these failures. Keep Temporal for multi-step, long-running or cross-service
orchestration. Replacing a Workflow with one retried callback would discard the
history and waiting semantics it depends on.

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

## Extract one operation without losing its workflow

Suppose a Workflow prepares a document, runs a publication Activity, waits for
approval and then notifies another service. Keep the preparation, approval and
notification orchestration on Temporal. Decide whether publication has a useful
independent Django owner.

For a new, locally owned publication, the Due example's admission is:

```python
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

It captures text transactionally, writes a revision-specific immutable object and
publishes the product key in fenced settlement. For a workflow occurrence that
must never coalesce with another publication, create a distinct request owner
with a stable key derived from the application's occurrence identity. The
example's one-row-per-document behavior is not an event history.

An Activity that submits to Due must be retry-safe at **admission**: a retry after
the Django commit must find the same occurrence rather than create another one.
The Workflow must wait for business completion, not merely successful admission.
Use Temporal's supported Activity and durable coordination APIs for that bridge;
do not call Django/provider I/O directly from deterministic Workflow code.

## Map the boundaries

| Temporal concept | Due migration decision |
| --- | --- |
| Workflow identity/history | Retain on Temporal; no Due history importer or replay engine exists |
| Activity making the provider call | Extract a native effect/operation only with clear occurrence ownership |
| Activity retries and heartbeat timeout | One layer owns provider retries; bridge retries only find/observe the same occurrence |
| Durable timer, signal, query or child Workflow | Retain Temporal or intentionally redesign persisted phase state |
| Activity return value | Deliver the final canonical product result, not an admission ID presented as success |
| Workflow cancellation | Define whether it stops waiting, cancels pending work or owes compensation |
| Worker/code-version rollout | Keep old executions compatible until drained; a source edit does not convert history |

## Pay attention to cancellation and replay

A Workflow cancellation cannot prove a provider request did not apply. Once Due
owns an admitted operation, cancellation needs a product policy: pending work may
be canceled where supported, uncertain work still needs evidence, and cleanup
may require its own obligation. Do not delete the row merely because a caller
stopped waiting.

Temporal and Due cannot both independently retry the provider call. If a bridge
Activity times out, its next attempt should find the same Due occurrence and
observe progress. Preserve the provider key of an existing operation; a new
engine's fresh key may duplicate it.

## Cutover and rollback

Prefer new executions or a clearly versioned branch using the bridge while
existing workflows continue on their original path. Keep old Activity handlers,
workflow history and result consumers. Deploy Due migrations, registration and
recovery before any Workflow can submit to it.

Do not convert running workflows by copying only their inputs. Completed
Activities, outstanding timers, signals and in-flight calls all affect what is
still owed. Any full rewrite needs explicit phase-state and history mapping and
is outside a thin migration.

Rollback redirects new work. Existing workflows that reference Due must retain
the bridge and completion lookup until their obligations settle or transfer
explicitly. Keep Due evidence and recovery alive for those occurrences.

## Could Due expose the Temporal interface?

No thin SDK shim can preserve workflow/service history, deterministic replay,
durable timers and signals merely by forwarding task calls. No such integration
ships. A bounded Activity bridge can be useful if it leaves native evidence and
product completion visible; an API that treats enqueue success as Activity
completion hides the essential difference.

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

Also crash between Django admission and Activity acknowledgement, retry the
bridge, cancel while the provider outcome is unknown, and interrupt completion
notification. Require one occurrence and correct workflow progress in every
history. Passing Due's local contract alone does not establish Temporal replay
or bridge correctness.

## Sources

[Temporal workflow execution](https://docs.temporal.io/workflow-execution),
[Python SDK guide](https://docs.temporal.io/develop/python).
