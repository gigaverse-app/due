# Coming from arq

arq is built around async functions and Redis jobs. Due is a Django-backed
obligation runtime executed through Celery. Migrate only when a product lifecycle
benefit justifies that runtime change; an async application does not gain by
replacing its enqueue spelling alone.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due can tie admission to a Django product transaction, distinguish changing
inputs from execution attempts, and recover fenced local completion. Its
non-repeatable operation modes explicitly retain uncertain provider outcomes.
Those can remove application recovery code around an arq job.

arq already expects jobs may run more than once under its pessimistic execution
model. Due does not eliminate the replay problem for arbitrary async functions.
Stay with arq for an async-first worker, shared event-loop resources and Redis
job/result behavior that already meet the requirement.

## Prepare the Due side

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


## The interfaces you will work with

| Familiar primitive | Its strength | Due counterpart and semantic change |
| --- | --- | --- |
| Coroutine in `WorkerSettings.functions` | Native asyncio jobs with worker context | Synchronous capability callback with an explicit async bridge if needed |
| `ArqRedis.enqueue_job()` → `Job` | Redis admission, explicit job IDs and deferral | Atomic Django producer returning captured work |
| `Job.result()`, abort, expiry, `ctx` | Per-job result/cancellation and shared async resources | Domain outcomes and work policy; no automatic ctx/abort contract |

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
import asyncio
from typing import Any
from arq.connections import ArqRedis
from .models import Document
from .storage import write_revision


async def publish(ctx: dict[str, Any], document_id: int, text: str, publication_id: str) -> str:
    key = await asyncio.to_thread(write_revision, text, publication_id)
    await Document.objects.filter(pk=document_id).aupdate(published_key=key)
    return key


class WorkerSettings:
    functions = [publish]


async def submit(redis: ArqRedis) -> str:
    job = await redis.enqueue_job("publish", 42, "Hello", "request-123")
    assert job is not None  # A duplicate explicit job ID may return None.
    key = await job.result(timeout=30)
    assert isinstance(key, str)
    return key
```

An arq coroutine usually receives resources in `ctx`. Due does not supply
that context or preserve the worker event loop. For an async provider helper,
call `run_effect_coroutine(async_helper, ...)` from `perform_effect` and create
resources with the correct loop lifetime; do not pass a coroutine object or
reuse a client tied to the old arq loop.

For an async HTTP caller, wrap the whole synchronous Due producer with
`sync_to_async(request_publication, thread_sensitive=True)` and await it. Keep
the transaction together. The gain is transactionally captured inputs and fenced
product completion; losing a native async worker model may outweigh it.

## After: model the obligation, split execution from completion

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


## Translate the contracts, not just the call

| Existing concept | Due migration decision |
| --- | --- |
| Registered async function name | Named capability retaining a compatible provider helper |
| Worker `ctx` / startup resources | Recreate typed worker-local dependencies; do not reuse an event-loop-bound client across processes |
| `_job_id` and uniqueness lifetime | Explicit occurrence key and retention; Due coalescing is a different contract |
| `_defer_until` / `_defer_by` | Normalize to aware `not_before`; scheduled-action mode for mutable deadlines |
| Job result / expiry | Persist required per-occurrence output; work/evidence retention is not result expiry |
| Retry and max tries | One Due retry authority, with tested counts and error disposition |
| Abort and cron | Specify pending cancellation and recurring identities separately; abort cannot retract provider acceptance |

## Pay particular attention to

The provider coroutine may be reusable, but the old worker's context lifecycle
is not transferred with it. Audit connection pools, HTTP clients, shutdown hooks
and cancellation behavior before choosing how to run it in the destination.
Due exposes `run_effect_coroutine` for a coroutine boundary; this is not an arq
worker or arbitrary event-loop compatibility guarantee.

If a job ID can be reused after its result expires, decide whether that means a
new business occurrence. Preserve original provider keys for existing work;
using a new Due-generated key can repeat an already-applied request.

## Cutover and rollback

Install Due and verify worker/recovery behavior independently first. Route new
occurrences of one function to it; let arq finish queued, in-progress, deferred
and retrying jobs with their existing contexts and result retention. Keep cron
ownership explicit during coexistence.

Quiesce old execution before any import, preserve job-to-occurrence mappings and
unknown provider outcomes, and do not move opaque runtime context into a payload.
Rollback changes new routing while Due remains responsible for admitted work.
Retain Redis results and old handlers until their consumers and delayed jobs drain.

## Can a thin adapter help?

A bound async admission facade is possible, but unimplemented. It could await
one transactional producer while rejecting unsupported arq options. It would not
provide WorkerSettings, `ctx`, abort or Redis Job semantics automatically.
A useful migration exposes native settlement/evidence; if all old async worker
semantics must remain invisible behind the facade, keeping arq is usually the
clearer choice.

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

Verify event-loop/resource lifetimes, coroutine cancellation after provider
acceptance, repeated job IDs across expiry, async result consumers and atomic
admission from the actual async request path.

## Sources

[arq 0.28 documentation](https://arq-docs.helpmanual.io/), [Due coroutine boundary](../../src/due/django/runtime/transport.py).
