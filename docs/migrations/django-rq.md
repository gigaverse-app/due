# Coming from django-rq

django-rq gives RQ a Django configuration, worker and administration surface.
That makes the product model a natural place to begin Due adoption, but it does
not make RQ jobs and Due obligations interchangeable. Migrate one business
operation while keeping existing RQ workers and result consumers intact.

[All migration guides](../migrations.md) · [How Due fits](../alternatives.md)

Reviewed **2026-10-11** against Due `65e0bcf`; upstream versions and source
snapshots are recorded in the [source register](../alternatives.md#source-register).
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
job = django_rq.enqueue(publish, document_id, text)

# After (with the Due example configured and the Document row already present):
from examples.documents.effects import request_publication

work = request_publication(document_id, text)
```

Existing `get_queue(...)`, `@job` and direct RQ submissions can bypass this
helper. Inventory all producers of the operation before declaring it migrated.
The replacement returns native work; admin links and result APIs for old RQ IDs
must remain routed to RQ.

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
