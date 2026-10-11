# Can Due provide another library's interface?

**A thin producer facade is feasible for a declared subset. A general drop-in
replacement is not.** The best first experiment is a Django Tasks backend. A
DBOS or Temporal shim would require a workflow engine; a full Procrastinate or
RQ shim would recreate substantial queue semantics.

This assessment describes **unimplemented proposals**, based on Due `65e0bcf`
and the [source audit dated 2026-10-11](competitors.md#source-register). It adds
no adapter modules, configuration paths, task decorators or migration commands.
Use [native migration](migrations.md) today.

## Define what compatible means

There are four different promises:

1. **Producer syntax:** familiar `.enqueue()`, `.defer()` or `.delay()` calls.
2. **Behavior:** identical occurrence identity, argument serialization, transaction
   timing, retries, ordering, cancellation and result/error contracts.
3. **Operations:** old workers, database rows, Redis keys, dashboards, CLI commands
   and administration continue to work.
4. **Advanced Due access:** users can express captured input, fenced settlement,
   uncertainty/evidence, projection repair and atomic follow-up admission.

A narrow facade may offer the first and a specified part of the second. It must
not imply the third. The fourth usually requires new application declarations;
keeping every old task body unchanged can conceal the main reason to adopt Due.

A facade is thin when it translates validated calls into existing admission,
execution and settlement APIs and projects their results. It stops being thin
when it owns another retry loop, lock service, scheduler, checkpoint log or work
truth. Count semantic obligations, not just lines in the first decorator.

## Per-library decision

The effort labels are architectural estimates, not implementation timings.

| Interface | Smallest useful subset | Work beyond a thin wrapper | Recommendation |
| --- | --- | --- | --- |
| Django Tasks | `BaseTaskBackend` for registered replay-safe functions; JSON arguments; one invocation identity; real TaskResult | Persist output/error/timing metadata; lifecycle projection; routing/options; transactions; validation/flags | **Prototype first.** Moderate bounded integration, not a one-method shim |
| Procrastinate | Explicitly bound task names and `.defer(**kwargs)` to Due admission | Cross-task execution locks, queueing-lock rejection, priorities, external DB connections, periodic tasks, job manager/CLI | **Conditional subset only.** Do not claim `App`/worker replacement |
| RQ | Named callable `.enqueue()` without dependencies or arbitrary pickled objects | Redis-backed Job/meta/results/TTL, registries, cancellation, callbacks, dependencies and pipelines | **Conditional subset only.** Job compatibility can dominate the project |
| Celery | Existing producer entry points delegate to named Due admission | Canvas, AsyncResult, request context, retry/ETA/expiry/revoke and per-call routing | **Prefer native adoption.** Celery already executes Due; another general task API adds little |
| Chancy | A named `push` binding | Cursor transaction semantics, unique keys, plugins, workflow graph, executor and queue controls | **Demand-driven only.** Existing durable enqueue reduces incremental benefit |
| PgQueuer | Named enqueue/payload conversion | Async driver transactions, batch contracts, serialized dispatch, completion watcher | **Demand-driven only.** Not a substitute driver |
| Dramatiq | Bound actor `.send` | Middleware, actor context, pipelines/groups and result plugin behavior | **Demand-driven subset** |
| Huey | Bound task call and fixed delay | Results, revoke/locks, periodic scheduler, pipelines | **Demand-driven subset** |
| arq | Bound function name and JSON-like arguments | `ctx` lifecycle, Redis job IDs/expiry, abort, cron and async worker contracts | **Low priority.** Async framework mismatch can erase the benefit |
| Taskiq | Bound `.kiq` producer | Broker plugins, dependency injection, middleware and result semantics | **Low priority; plugin fidelity unassessed** |
| Django Q2 | Bound `async_task` | Broker-dependent delivery, `q_options`, hooks, results, groups/chains and cron | **Demand-driven subset** |
| DBOS | Explicit bridge to one Due-owned occurrence | Step checkpoints, deterministic replay, durable waits/messages, transaction tracking, workflow handles | **No general shim.** Keep workflow engine; bridge bounded operations |
| Temporal | Activity/domain boundary calling one Due producer | Workflow history/service protocol, replay, timers, signals, children, cancellation | **No SDK shim.** Interoperate only |
| PGMQ | Domain producer replacing one message type | Visibility windows, batch read/delete/archive and compatibility with other consumers | **No client shim.** Migrate the consumer lifecycle explicitly |

The library-specific [migration recipes](migrations.md) identify the actual
contracts to inventory. These decisions apply to the audited versions, not every
past/future release or third-party extension.

## Which Due advantages survive a wrapper?

| Benefit | Unchanged function behind a generic task wrapper | Explicit binding to a native capability |
| --- | --- | --- |
| Product and obligation commit atomically | Only if admission is moved into the same supported Django transaction | Yes, through the declared producer boundary |
| Lost wakeup / execution recovery | Yes, after durable admission and correct registration/recovery setup | Yes |
| Fenced completion | Wrapper-owned bookkeeping only; DB writes hidden inside the old function are not fenced | Product completion callback is fenced |
| Provider idempotency | Cannot infer it or insert a key into arbitrary SDK calls | Application supplies stable provider identity and replay contract |
| Mutable desired state | Per-invocation tasks do not automatically become projections | Available via `Projection` and its repair policy |
| Movable/cancellable deadlines | A fixed delay is possible; old revoke semantics need separate treatment | Available via `ScheduledAction` and eligibility |
| UNKNOWN/no blind resend | Cannot safely wrap an arbitrary function as replay-safe or synthesize its receipts | Available through operation/evidence declarations |
| Independently recoverable cleanup | A legacy `finally` block remains vulnerable to process death | Available when compensation is persisted before crossing |
| Atomic successor handoff | A legacy callback publishing a second message still has a gap | Admit successor in settlement |
| Application conformance proofs | Runtime wrapper proofs do not establish task-body correctness | Bind real admission, external crossing and settlement |

A source-compatible wrapper can therefore be a **useful first stage**, but it is
not the complete reliability migration. It is a dead end if the public API has
no route from “invoke this function” to those explicit declarations.

## Django Tasks backend proposal

Django's [backend contract](https://docs.djangoproject.com/en/6.0/ref/tasks/)
is the intended extension point. Preserve `Task`/`TaskResult` from Django rather
than inventing lookalikes. Check the installed minor version; the audited
[source](https://github.com/django/django/tree/52e5ba169694cbd70c538ce62275cd2abd7647fd/django/tasks)
and live docs can evolve independently.

### Admission and identity

Begin with an allowlist of replay-safe, synchronous module-level functions.
Record the function identity, normalized JSON arguments and an immutable
invocation identifier in a concrete invocation owner. Use one concrete Due work
model for that invocation family and one registered capability. Each enqueue
creates a new invocation, even when arguments match. Desired-state coalescing is
a separate native binding, not a hidden optimization.

Persist the invocation and work inside the caller's default Django transaction;
register the wake for after commit. Return an admission result without running
the function. A later rollback must remove both rows and the wake. Do not keep
only an in-memory callback until commit. A returned ID inside an uncommitted
transaction is provisional; after rollback lookup must report it missing.

These invocation/result models do not exist in Due today. They add migrations,
retention and API compatibility responsibilities even though the coordination
engine is reused. Do not create dynamic Django model classes per decorated
function at import time.

### Execution and results

Delegate claim, lease, execution, retry, recovery and finalization to Due. Store
the normalized return value in the same fenced transaction as successful
settlement. Store required task metadata where the corresponding transition is
owned; if Due lacks a public hook for required information, extend the shared
owner or exclude that feature rather than maintaining a competing state machine.

The result model must account for errors, first start, last attempt, completion,
worker identities and attempt counts where the selected Django contract requires
them. Due's `attempt_count` and failure-code fields are not automatically an
accurate substitute for Django's result history. A crash between function return
and settlement can rerun a replay-safe function; this profile does not promise a
durable checkpoint of arbitrary return values.

Implement `enqueue`, `get_result`, their async surfaces, task validation and
correct Django exceptions. The standard base provides async wrappers, but that
does not imply arbitrary coroutine task execution or transaction propagation
across independently scheduled calls. Initially reject coroutine bodies,
non-default priority and unsupported options using validation/feature flags.
Reject `takes_context=True` until its attempt/result metadata is correctly
populated; later support it with Django's real `TaskContext`.

Use one supported queue initially. Due registers a work model once with a fixed
route; arbitrary per-invocation `.using(queue_name=...)` cannot simply override
that registration. Multiple queues require deliberately supported routing, not
ignored user options. Likewise, do not set `supports_priority=True` just because
a Celery broker accepts a priority argument: Due's recovery selection must
preserve the promised ordering too.

A later fixed-delay profile can map `run_after` to persisted `not_before` and
set `supports_defer` only once recovery respects it. This is distinct from
business cancellation and movable deadlines.

### Status projection

For a replay-safe invocation profile, a candidate projection is:

| Due state | Django task status | Required detail |
| --- | --- | --- |
| READY, QUEUED, RETRYABLE | READY | A retry remains unfinished; preserve attempt/error metadata |
| CLAIMED | RUNNING | Expose the actual lifecycle; don't infer liveness from an old result object |
| SUCCEEDED | SUCCESSFUL | Result was persisted with fenced completion |
| EXHAUSTED, TERMINAL_FAILED | FAILED | Preserve the stopped reason and result-access errors |
| UNKNOWN | **Not representable faithfully** | Standard API has no uncertainty state; exclude evidence-required tasks from this profile |

Do not translate UNKNOWN to FAILED and invite ordinary retries, or call it
RUNNING indefinitely while hiding the need for evidence. Evidence-required native
bindings need a separate obligation view/API that exposes uncertainty. A
Django TaskResult may describe an admission helper completing, but must then be
clearly distinguished from the underlying business operation completing.

### Native upgrade path

A future integration should allow registration-time binding from a frontend task
to an application admission function and capability, with explicit input and
result projection. **This is a design requirement, not a proposed shipped API
name.** It must expose the native work identity and supported capability to
operators and application code without requiring import from Due's private
runtime. Native functions can coexist beside standard tasks in the same app.

For existing invocation work, keep those occurrences under their old capability.
Move new admissions to the richer domain model only when identity and result
contracts are mapped. Switching the implementation of a task must not reinterpret
already-persisted payloads. Use versioned bindings and retain old handlers until
all their work and required result lookups drain.

A useful exit is: standard replay-safe task → explicit external/local split →
selected native projection/operation/schedule, while callers either retain a
truthful result contract or migrate to a richer one. If the adapter insists that
all obligations remain arbitrary functions with four task statuses, stop there;
it cannot expose Due's evidence model honestly.

## Procrastinate and RQ: what a small bridge can promise

Use an explicit compatibility namespace or application-owned facade; do not
shadow the upstream package or monkeypatch its classes. Bind a fixed set of
existing task names to real Due producers. Preserve supported arguments and
reject unsupported options **before admission**.

For Procrastinate, `.defer()` can return a mapped integer identifier only if its
lifetime, occurrence identity and lookup behavior are specified. The same number
as `work.pk` does not make it a Procrastinate job. Cross-task `lock`,
`queueing_lock` rejection, priority ordering and `connection=` are not aliases
for Due claims, coalescing, queue routing or Django transactions. A facade that
rejects them can be useful for audited call sites; it is not globally compatible.

For RQ, a method called `.enqueue()` without a faithful Job/result contract is
only source-compatible for callers that ignore the return. Do not silently
accept `depends_on`, callback, serializer, timeout, retention or meta arguments.
If a migration needs extensive Job emulation, prefer changing callers to domain
results over building a Redis-shaped database facade.

Both bridges need real per-occurrence identities unless the application explicitly
requests coalescing. They must also make the native capability available; otherwise
they keep the task body's unfenced writes and uncertainty policy hidden. Keep old
engine result lookup separate from new obligation lookup, with an explicit ID
mapping when clients need continuity.

## A bounded prototype and its stop conditions

Build no universal adapter framework first. Start with one Django Tasks backend
profile and two application cases: a replay-safe function with a JSON result,
and a native publication capability with fenced product settlement. Use a
version-pinned upstream contract suite as the frontend oracle and Due's actual
coordinator as the engine.

| Acceptance check | What it must establish |
| --- | --- |
| Same JSON arguments twice | Two independent invocation IDs/effects; no accidental coalescing |
| Transaction rollback / savepoint rollback | No leaked invocation, work or execution |
| Lost initial publication | Real recovery completes and result lookup is correct |
| Worker death after effect, before settlement | Replay behavior is declared; stale product publication is rejected |
| Failed attempts / exhausted budget | Result states, counts, error metadata and explicit rearm match the promised subset |
| Unsupported queue/priority/context/async option | Deterministic validation error before rows are admitted |
| New producer plus old IDs | Versioned routing and result lookup remain coherent |
| Native advanced case | A real richer capability remains usable without bypassing the public integration |

Proceed only if this removes application recovery code or closes a demonstrated
failure, and the frontend contract can be implemented through shared runtime
owners. Stop if it needs a second recovery engine, loses original ordering or
results, obscures UNKNOWN, or requires incompatible infrastructure solely to
preserve familiar spelling.

A successful prototype would justify an implementation proposal and supported
version matrix. This documentation audit alone does not establish adapter
compatibility, runtime cost, or production readiness.
