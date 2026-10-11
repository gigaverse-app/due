# Coming from RQ

Start with an RQ job that owns a product-side effect: publish an artifact, update
a provider resource, or complete a requested operation. Due can make that
obligation part of the product transaction and keep completion recoverable after
a lost message or worker death. Keep ordinary jobs on RQ when its function-and-
result model is already sufficient.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What changes for the better

A PostgreSQL product update followed by Redis enqueue has two commit boundaries.
Due can replace that boundary with one product/work transaction; periodic
recovery supplies a lost wakeup. If you already have a correct transactional
outbox, the gain is replacing its maintenance burden, not inventing durability.

For overlapping publications, Due can prevent an older worker from publishing
current product completion. For non-repeatable requests, its operation modes
retain UNKNOWN until evidence settles the outcome. These gains require splitting
the task body and declaring the right contract; a callable wrapper cannot infer
whether an email, payment or provider request is safe to repeat.

Stay with RQ for independent computation, existing Job/result consumers or
substantial dependency graphs. Due requires a Django/PostgreSQL application and
Celery; switching is not a way to remove worker infrastructure or necessarily Redis.

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
job = queue.enqueue(publish, document_id, text)

# After (with the Due example configured and the Document row already present):
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

Replace consumers of `job.result`, metadata and status deliberately. For this
publication, `Document.published_key` is the durable business result. If callers
need one result for every invocation, model a publication-request owner and
persist its result in fenced settlement; a single mutable document pointer is
not an equivalent historical result store.

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

## Map the RQ contract

Inventory `Queue.enqueue[_at/_in]`, callable import paths, custom serializers,
`job_id`, `Retry`, callbacks, dependencies, `Job.meta`, result/failure retention
and cancellation. RQ's Redis job record is not Due's work row. A Redis pipeline
cannot join a PostgreSQL product transaction.
[Queue API](https://python-rq.org/docs/),
[jobs](https://python-rq.org/docs/jobs/),
[retry behavior](https://python-rq.org/docs/exceptions/).

Replace selected producers with the admission function above. Persist arguments
as declared model fields or a deliberately validated payload; do not blindly
unpickle old job payloads into a new generic executor. Put results required by
callers in canonical application state during settlement. Port a callback that
performs an effect to another obligation, and a dependency to an explicit
completion condition/handoff. `Dependent` wakes existing work; it does not create
RQ's dependency/result graph automatically.

Normalize deferred times explicitly: RQ accepts naive local datetimes, whereas
Due's `not_before` requires an aware datetime. Preserve the original intended
timezone when translating, especially across daylight-saving transitions.
[RQ scheduling](https://python-rq.org/docs/scheduling/).

Translate execution timeouts to provider timeouts and Due's registered worker
limits, with the lease longer than the hard worker limit. Translate retry counts
by tested histories, not matching numeric names: Due distinguishes delivery
reservation recovery from failed execution. Result TTL and the obligation's
retention have different meanings; retain uncertain evidence independently.

Drain the selected RQ queue/registry cohort before switching ownership. Scheduled,
deferred, failed and started jobs all need a disposition, not just the ready queue.
Keep old result lookup alive for IDs already returned to clients. Redis may still
be needed as Due's Celery broker, so this migration does not necessarily remove it.

## Cutover and rollback

Route a chosen set of new occurrences to Due after its worker and recovery are
live. Drain old ready, scheduled, deferred and started jobs under RQ; explicitly
dispose of failed jobs instead of silently resetting their retry budgets. Keep
old callable paths and result lookup for IDs returned before cutover.

If transferring jobs, stop their old execution/retry path, retain a unique
old-job-to-new-owner mapping and preserve original provider keys. Do not replay
a possibly applied operation as fresh work. During rollback, change routing for
new admissions only; Due must finish or deliberately transfer its admitted
obligations. Never have RQ and Due independently retry the same imported job.

## Could you keep `Queue.enqueue()`?

A facade for an allowlist of callables is possible; no adapter ships today.
It is small only when callers do not require a faithful Redis-backed `Job`.
Results, metadata, TTL, registries, callbacks, cancellation and dependencies make
full Job emulation a separate project. Unsupported options must fail before
persisting an invocation, not disappear silently.

A useful facade exposes the native capability and preserves each invocation's
identity. Upgrading selected jobs to explicit settlement or evidence handling
then remains possible. A facade that runs arbitrary old functions only fences
its own bookkeeping; the functions' internal product writes remain unprotected.

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

Exercise Job consumers, dependency completion, callback failure, custom serializer
round trips, two equal submissions, result expiry and delayed-time conversion.
Confirm every accepted old/new ID has one execution owner and a usable lookup.
