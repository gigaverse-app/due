# When Due is the right tool

Due is useful when a background operation must remain consistent with changing
application data. It gives a Django application shared mechanisms for remembering
what is owed, rejecting obsolete local completion, retaining uncertain external
outcomes, and recovering the remaining work.

Its strongest fit is an application that would otherwise repeat those mechanisms
across several integrations. An ordinary task queue can be simpler for independent,
safely repeatable jobs. A workflow engine can be clearer for a process whose main
requirement is remembering completed steps, waiting for events, and resuming its
control flow.

These are choices about application semantics. Due does not claim to be faster,
more scalable, or universally more reliable than other libraries.

## Start with three questions

Suppose a user edits a document while its previous version is being published.
Three facts can legitimately disagree:

| Question | Example | What Due represents |
| --- | --- | --- |
| What does the application want now? | Publish version B. | Captured inputs and a desired revision. |
| What happened at the external service? | Version A's upload succeeded, but its response was lost. | A provider result or an explicitly uncertain attempt, depending on the capability. |
| What may this worker still publish locally? | The worker for A must not replace B's published pointer. | A captured ownership token and revision checked at settlement. |

A worker losing permission to update the product does not erase an external
effect it already caused. Conversely, learning that an old operation succeeded
does not make its result the current desired state.

Due makes these distinctions explicit. Your application still decides which
version to publish, which provider evidence to trust, and whether an obsolete
output needs cleanup.

## Where Due helps

### Keep acceptance and follow-up work in one transaction

The application commits its product change and work obligation together. For
example, saving a document revision also records that publication is owed. If
the transaction rolls back, neither survives. If the commit succeeds and the
worker notification is lost, periodic recovery can discover the work.

The same rule applies at completion: a settlement callback can update the product
and admit a required follow-up in one transaction. A notification accelerates
execution; it is not the only record of the obligation.

This fits applications whose authoritative state already lives in Django and
PostgreSQL. It is **not unique to Due**: transactional PostgreSQL queues and a
correct outbox can provide durable admission too. The
[Procrastinate comparison](../examples/comparison/README.md) exercises that
shared guarantee. Due becomes more useful when admission must compose with
revisions, evidence, cleanup, or guarded product completion.

### Prevent an obsolete result from becoming current

In the [document example](../examples/documents/README.md), each publication
writes an immutable file and then publishes its key on the document row.
`ReplaySafeEffect` separates the external write from `finalize_success`, the
local completion callback. Due checks the execution's authority before that
callback commits.

If a newer revision supersedes an old worker, the old worker cannot publish its
pointer through that callback. Its file may still exist. Immutable output names
and any required cleanup remain application responsibilities.

This is a local database guarantee. Putting an arbitrary function behind a Due
adapter does not protect database writes performed inside that function; product
completion must use the guarded settlement boundary.

### Express uncertainty without treating it as permission to retry

Some operations can be repeated safely. Others create another purchase, message,
or physical order each time they are submitted. Choose the capability from the
provider's actual contract.

The [print-order example](../examples/orders/README.md) deliberately models a
printer where each submission creates another order. If the printer commits an
order and its reply is lost, `ObservedExternalOperation` keeps the attempt
UNKNOWN and queries for a correlated receipt. Once a receipt is stored, local
completion can recover without another submission.

This prevents blind replay; it does **not** guarantee eventual completion. If
no reliable evidence becomes available, the operation may remain unresolved and
need an operator decision. A crash just before sending can be indistinguishable
from a crash just after acceptance. Safety can therefore cost availability.

Prefer a provider-enforced idempotency key when its scope and retention satisfy
your requirements. A searchable reference is useful for correlation, but does
not itself prevent duplicate effects.

### Repair a changing external view

The [mutable catalog example](../examples/catalog/README.md) updates a search
entry. Its simulated provider has no revision check: an older write can finish
after a newer write and leave the external entry stale.

`Projection` retains the relevant attempt evidence and schedules repair using the
current desired value. Local fencing protects product completion; repair handles
the separately mutable external view.

This is eventual convergence under the declared provider and recovery policy.
It does not establish strict remote ordering or prevent a temporary stale value.
A provider-side conditional write may solve the problem more directly. A bounded
repair window limits effort; elapsed time alone cannot prove that an old remote
request has finished.

### Retain cleanup and late evidence independently

An output may need deletion even if the worker that created it died or lost its
authority. `IndependentCompensation` can record that cleanup obligation before
the external call. Evidence-required operations can also retain an exact late
receipt without letting an obsolete execution publish current product state.

Use these mechanisms when the provider and product lifecycle actually require
them. Due does not require every application to speculate about replacement
operations or build a general resource reconciler. See the
[capability guide](../package/docs/capabilities.md) for the smallest suitable interface.

## What another tool may do better

| Primary requirement | A strong starting point | What changes the decision toward Due |
| --- | --- | --- |
| Run independent, repeatable functions with retries. | A task queue such as Procrastinate, Celery, RQ, or Dramatiq. | Repeated application code for changing intent, stale completion, external uncertainty, or cleanup. |
| Commit a job with a PostgreSQL product update. | Procrastinate or another transactional queue. | Durable admission alone is insufficient reason to migrate. Compare the additional semantics you need. |
| Preserve step results and resume a multi-step process. | DBOS or Temporal. | A bounded operation inside that process benefits from Due's product-state and evidence rules. |
| Coordinate a long-lived entity through messages and timers. | Consider a Temporal workflow as the entity's owner. | Product decisions must remain atomic with existing Django rows and related obligations. |
| Expose a standard Django task interface. | Django Tasks with a suitable execution backend. | Native Due capabilities are needed beyond the generic task contract. |

Temporal can model a controller that receives messages, not only a linear
pipeline. DBOS supports transactional workflow admission as well as checkpointed
execution. Both can implement reconciliation and compensation. Due's potential
advantage is the reuse of focused application mechanisms, rather than an inability
of workflow engines to express the same behavior.

Completed workflow steps need not execute again during recovery. That is a real
advantage for expensive, multi-stage processing. It does not close the interval
between an external service acting and the engine durably recording the result.
External calls still need their own replay or evidence policy. See
[Temporal's Activity semantics](https://docs.temporal.io/activity-definition#idempotency),
[its message-driven workflow model](https://docs.temporal.io/develop/python/workflows/message-passing),
[DBOS's workflow guarantees](https://docs.dbos.dev/python/tutorials/workflow-tutorial#workflow-guarantees),
and [DBOS's transactional admission](https://docs.dbos.dev/python/examples/outbox).

When PostgreSQL owns product decisions, a separate workflow authority needs an
explicit way to exchange those decisions reliably, such as an outbox or durable
discovery. When the workflow itself should own the process, moving authority
there may simplify the application. Decide that boundary before comparing the
number of decorators or lines of code. Due and a workflow engine can also own
different parts of the same application, provided each obligation has one owner.

## What adopting Due costs

- **Explicit application structure.** You define concrete work models, captured
  inputs, external calls, and local settlement. Migrations and registration are
  more work than decorating a simple function. That structure should remove
  meaningful application coordination, not just add ceremony.
- **A specific runtime.** The current alpha requires Python 3.14+, Django
  >=6.0.8,<7, PostgreSQL, Celery, a broker, workers, and periodic recovery.
  Only work-table composition is public. A PostgreSQL-only queue can have fewer
  moving parts. See [integration](../package/docs/integration.md) and [extraction scope](extraction.md).
- **Database and recovery work.** Claims, transactions, evidence, retained rows,
  and provider observations have costs. Recovery cadence trades promptness
  against load; workers and recovery must be monitored. There is no comparative
  throughput or cost result that makes Due the default performance choice.
- **No checkpointed Python workflow.** Due does not automatically save arbitrary
  local variables and intermediate results. Applications explicitly persist the
  facts and handoffs they need. Complex orchestration may be clearer elsewhere.
- **Policy remains yours.** Due does not choose retry budgets, decide that an
  empty inventory proves absence, or make a provider operation repeatable.
  An UNKNOWN outcome or visibly stopped obligation may require investigation.

The [operations guide](../package/docs/operations.md) explains how to observe the runtime. The
[architecture reference](../package/docs/architecture.md) defines the guarantees and boundaries.

## Check the failure model before paying for it

A simulated provider can do anything its author programs it to do. A test that
releases an old response later proves how the application handles that history;
it does not establish that a particular provider can produce it.

For a proposed recovery mechanism, establish:

1. **The failure boundary.** Did the provider accept the request before the
   caller lost the reply, or was the request rejected before any effect?
2. **The available evidence.** Could an inventory query, webhook, immutable
   output, or idempotency-key lookup already resolve the uncertainty? Explain
   which channels must fail for the proposed history to remain possible.
3. **The application's choice.** Waiting for evidence and authorizing a new
   occurrence are different policies. Creating a replacement during uncertainty
   can introduce duplicates and cleanup obligations that waiting would avoid.
4. **The evidence level.** Distinguish a production incident, a controlled run
   against a real provider, a conclusion from versioned provider source, and a
   simulator-only case. None silently substitutes for another.

This repository's printer and catalog are local simulations with real independent
storage operations. Their tests demonstrate Due's behavior against those declared
contracts, not a failure rate or consistency contract for an external vendor.
The [conformance suite](../package/docs/conformance.md) helps test application bindings; it cannot
certify assumptions about a provider it does not execute.

Adopt the smallest capability that addresses a supported requirement. If an
existing queue plus provider idempotency already gives the desired result, keep
that simpler design. If several integrations repeatedly need guarded product
completion, retained evidence, and recoverable follow-ups, Due gives those
mechanisms a shared home.

For a concrete code migration and the semantics to preserve, continue with
[your library's guide](../package/docs/migrations.md).
