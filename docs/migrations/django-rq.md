# Coming from django-rq

django-rq gives RQ a Django configuration, worker and administration surface.
That makes the product model a natural place to begin Due adoption, but it does
not make RQ jobs and Due obligations interchangeable. Migrate one business
operation while keeping existing RQ workers and result consumers intact.

[All migration guides](../migrations.md) · [How Due fits](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](https://github.com/gigaverse-app/due/blob/main/docs/alternatives.md#source-register).
This is a migration guide, not an implemented compatibility integration.

## What you gain, and what you keep

Due can commit a product change and its work together, then recover if the
broker wakeup is lost. A post-commit Redis enqueue alone has no durable memory
of a process dying before it runs. If your application already supplies an
outbox, the migration can replace that protocol rather than add another one.

Fenced local completion and provider-evidence modes are further benefits when
your current job maintains those mechanisms itself. Stay with django-rq when its
simple job API, Django admin integration and existing results are enough.
Due requires Celery and concrete work models; Redis may remain as the broker.

## Prepare the Due side

Follow the [shared prerequisites and setup](../migrations.md#prepare-the-due-side),
then preserve the source-specific behavior below. The complete document example
belongs to this repository; your application owns its models and producer.


## The interfaces you will work with

| Familiar primitive | Its strength | Due counterpart and semantic change |
| --- | --- | --- |
| `@job`, `.delay()`, `django_rq.enqueue()` | Django configuration around RQ functions and queues | Native capability/producer and a Due work registration |
| RQ `Job`, registries, Django admin | Job-level inspection and operational controls | Domain/work inspection; the old admin does not manage Due obligations |
| `get_queue("name")`, `RQ_QUEUES` | Named Redis connections and execution queues | Registration selects the Celery queue for a concrete work model |

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
from django_rq import job
from .models import Document
from .storage import write_revision


@job("publication")
def publish(document_id: int, text: str, publication_id: str) -> str:
    key = write_revision(text, publication_id)
    Document.objects.filter(pk=document_id).update(published_key=key)
    return key


job = publish.delay(42, "Hello", "request-123")
# Later, after successful execution:
key = job.return_value()
```

The decorator and `.delay()` disappear for the selected operation. Keep the
application's useful storage function; move its ORM completion into
`finalize_success`. `RQ_QUEUES` cannot configure Due's Celery transport, and the
Django RQ admin still displays only RQ jobs.

The gain is that a lost post-commit wake-up no longer loses the only record of
owed publication: Due recovery scans the committed work row. Preserve existing
outbox/recovery guarantees if your current RQ integration already supplies them.

## After: model the obligation, split execution from completion

Use the [shared native implementation](../migrations.md#shared-native-implementation)
for the concrete model, atomic producer, effect and fenced settlement. It is the
same target used by these migration guides. Adapt its owner identity to the
semantics described here; a current-state obligation cannot replace an
append-only task history without changing behavior.


## Translate the contracts, not just the call

| Existing concept | Due migration decision |
| --- | --- |
| `django_rq.enqueue` / queue `.enqueue` / `@job` | One canonical Due producer used by every selected admission path |
| `RQ_QUEUES` and default timeout/result TTL | Explicit Due registration, worker limits and independent result retention |
| Commit-time enqueue behavior | Insert Due work inside the product transaction; defer only its notification |
| RQ Job, meta and result lookup | Persist the needed domain outcome or per-occurrence result; preserve old lookups |
| Exception handlers / callbacks | Retain reporting; move required effects to durable successors |
| Scheduled/deferred jobs and dependencies | Keep RQ ownership or explicitly model eligibility and handoff |
| RQ admin/worker commands | Operate both systems during cutover; Due does not replace their backend storage |

## Pay particular attention to

A wrapper around `django_rq.enqueue` catches only that entry point. A decorator,
management command, scheduled job or direct Queue call may still admit old work.
Map all paths to one authoritative producer for the chosen cohort.

Preserve custom `job_id`, serializer, result lifetime and dependency contracts.
Equal submissions to a one-row-per-document capability may coalesce; create a
request owner per occurrence when every RQ job must execute. For scheduled work,
convert local/naive timestamps deliberately to aware timestamps.

## Cutover and rollback

Deploy Due tables, registrations and workers/recovery first. Route new
occurrences of the selected operation to Due and drain old RQ queue entries,
started jobs, scheduled/deferred registries and retries. Keep django-rq admin
and old handlers for the remaining cohort; do not remove `RQ_QUEUES` globally.

Any import must quiesce the old execution/retry path, keep a unique mapping and
preserve provider keys and unknown outcomes. Rollback changes new admission
routing while Due finishes its existing work. Retire old result storage only
after all promised lookups expire or have an explicit replacement.

## Can a thin adapter help?

A facade for a few producer helpers can reduce source changes, but none ships.
It does not reproduce RQ Job, admin, decorators or custom queue classes. Reject
unsupported options before admission. Prefer an explicit application producer
when preserving a helper name would obscure the new transaction boundary.

Keep native capability access so the task can evolve into split execution and
settlement, or evidence-required operation. Otherwise only the wrapper's status
benefits from fencing, while the old task's writes remain outside it.

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

Test every producer path, request transaction rollback, old admin/result URLs,
custom job IDs, dependency completion and retained exception reporting. During
rollback, prove that both producer versions cannot admit the same occurrence
under independent identities.

## Sources

[django-rq](https://github.com/rq/django-rq), [RQ jobs](https://python-rq.org/docs/jobs/), [RQ scheduling](https://python-rq.org/docs/scheduling/).
