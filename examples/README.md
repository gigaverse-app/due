# Runnable applications

Start with the [task tutorial](../package/docs/quickstart.md): submit a function,
read its result, kill its worker, and recover the job. The
[prepared greeting app](hello_due/README.md) supplies setup. Then use the
[numbers app](numbers/README.md) to inspect native admission and completion hooks,
or choose an application below for its product behavior.

Examples live alongside Due so API changes and the applications that exercise
them can be reviewed together. Most share `examples.settings`,
`examples.celery_app`, and the root `compose.yaml`. Printworks is standalone and
has its own setup.

## Django Tasks to native capabilities

[Acme Printworks](acme_printworks/README.md) grows from a word-count task into
proof publication, portal repair, uncertain print ordering and independent
cleanup. Follow the [step-by-step tutorial](../docs/acme-printworks-tutorial.md)
and run its application contracts as each guarantee is introduced.

## Document publication

The [document application](documents) writes an immutable file and publishes its
key on a Django model. Follow the [root quickstart](../README.md#run-the-example).
Its [worker test](../integration_tests/integration/test_celery_worker.py) drops the initial
notification and checks recovery through PostgreSQL, Redis and a Celery worker.

## Books and search

The [books application](books/README.md) adapts Procrastinate’s Django books
demo, with upstream attribution in its README. It has a browser interface and a two-stage
indexing workflow. It uses a local SQLite search provider and demonstrates
recovery when either stage's notification is lost. Its tests import the same
application code and include generated conformance, actual process death, and
Redis/Celery integration journeys.

## Scheduled publication

The [scheduled publication application](scheduled/README.md) adapts Temporal’s
updatable timer scenario. It adds a browser/CLI, real file publication, movable
deadlines, cancellation, and recovery after a worker restart. Its README pins the
upstream provenance and separates the shared scenario from our additions.

## Celery migration add-on

The [Celery comparison](celery_migration/README.md) uses the document app’s model
and file provider to compare a plain task with native Due. It is separate from
the Books/Procrastinate port and keeps both original examples intact.

## Mutable state, uncertain operations and comparison

- [Mutable catalog](catalog/README.md): edit book metadata, fence stale product
  completion and repair an escaped older provider write.
- [Print orders](orders/README.md): retain an accepted-but-unknown occurrence;
  observe its receipt without submitting another physical order.
- [Procrastinate comparison](comparison/README.md): the same immutable-book
  provider/outcomes under transactional rollback and actual process death.

Run `DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django due_check`
to check the local registration, task imports and Beat routes before starting.
See [operations](../package/docs/operations.md) for worker recovery freshness evidence.

## Adding an example

Keep application code here and import that same code from tests. Use Due's public
API and enroll capabilities in its conformance suite. Each application README
must explain its business outcome, setup, failure controls and observable result.
Keep UI/provider dependencies out of Due's runtime dependencies.

## Executable provider bindings

The six capability declarations in these five applications use the public
[`ProviderSeam` API](../package/docs/conformance-api.md#derive-routine-bindings-from-one-provider-seam).
The same provider handles supply independently read outcomes, per-work call
counts and scoped refusal/reply-loss faults. Generated proofs run real admission,
workers, settlement and recovery.

```text
application command -> persisted work -> real worker -> file / SQLite / printer
                            ^                |                    |
                            |                v                    v
                         recovery       settlement       independent observation
```

| Application | Derived proofs and explicit histories |
| --- | --- |
| Books, both stages | Delivery, bounded retry and replay; actual process death around indexing, handoff and publication |
| Mutable catalog | Delivery, retry, replay and captured-revision convergence; overlapping writes retain their separate histories |
| Documents | Delivery, retry, replay and file observations; immutable-input and producer rollback histories |
| Scheduled publication | Delivery, retry and replay; deadline movement, cancellation and immutable file observations |
| Print orders | Delivery and bounded retry; scoped refusal, accepted-but-lost response, receipt observation and process death |

Print orders intentionally do not derive harmless replay: a second physical
submission is a different effect. Catalog convergence tests a captured settled
revision; it does not claim that arbitrary provider writes commute. Application
CI checks complete generated proof inventories as well as the resulting outcomes.

The [Widget Store](widget_store/README.md) ports the DBOS checkout scenario: stock contention, payment gating, compensation, checkpointed dispatch, and a real crash/restart UI.
