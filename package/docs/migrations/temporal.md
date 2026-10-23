# Coming from Temporal

Treat this as a guide to moving a bounded product operation, not replacing a
Temporal application. Temporal's durable workflow history, replay, signals and
timers solve a broader orchestration problem. Due may simplify one Django-owned
effect inside that system while the workflow continues to own the process.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
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

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


## The interfaces you will work with

| Familiar primitive | Its strength | Due counterpart and semantic change |
| --- | --- | --- |
| `@workflow.defn`, Workflow execution/history | Durable control flow, timers, signals and replay | No whole-Workflow counterpart; retain orchestration |
| `@activity.defn`, Activity result/retry | External work with timeout and retry policies | A selected operation can become a native Due capability |
| `WorkflowHandle`, cancellation/queries | Interact with a durable execution | No handle compatibility; define an explicit domain observation/bridge contract |

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
from datetime import timedelta
from dataclasses import dataclass
from temporalio import activity, workflow
from temporalio.client import Client


@dataclass
class PublicationInput:
    document_id: int
    text: str
    publication_id: str


@activity.defn
async def publish_blob(data: PublicationInput) -> str:
    import asyncio
    from .models import Document
    from .storage import write_revision

    key = await asyncio.to_thread(write_revision, data.text, data.publication_id)
    await Document.objects.filter(pk=data.document_id).aupdate(published_key=key)
    return key


@workflow.defn
class PublishDocumentWorkflow:
    @workflow.run
    async def run(self, data: PublicationInput) -> str:
        return await workflow.execute_activity(
            publish_blob,
            data,
            start_to_close_timeout=timedelta(minutes=2),
        )


async def submit(client: Client) -> str:
    return await client.execute_workflow(
        PublishDocumentWorkflow.run,
        PublicationInput(42, "Hello", "request-123"),
        id="publication-request-123",
        task_queue="publication",
    )
```

`execute_workflow()` waits for the Workflow result; the Due producer below
returns when a database obligation has been admitted. Those calls are not
substitutable. A retained Workflow must observe actual completion through a
retry-safe Activity/coordination boundary, not execute Django I/O in Workflow code.

The example has one Activity. Replacing an entire Workflow containing timers,
signals or children would discard those semantics. Consider Due only for a
bounded Django-owned operation whose local product completion needs fencing.
Temporal already offers durable recovery; the gain is the domain settlement
boundary, not the mere existence of retries. Keep a distinct occurrence owner
when each Workflow execution owes a separate result.

## After: model the obligation, split execution from completion

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


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
