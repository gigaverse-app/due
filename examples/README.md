# Runnable applications

Examples live alongside Due so changes to the public API and the applications
that exercise it can be reviewed and tested together. They share
`examples.settings`, `examples.celery_app`, and the root `compose.yaml`.

## Document publication

The [document application](documents/) writes an immutable file and publishes its
key on a Django model. Follow the [root quickstart](../README.md#run-the-example).
Its [worker test](../tests/integration/test_celery_worker.py) drops the initial
notification and checks recovery through PostgreSQL, Redis and a Celery worker.

## Books and search

The [books application](books/README.md) has a browser interface and a two-stage
indexing workflow. It uses a local SQLite search provider and demonstrates
recovery when either stage's notification is lost. Its tests import the same
application code and include generated conformance, actual process death, and
Redis/Celery integration journeys.

## Mutable state, uncertain operations and comparison

- [Mutable catalog](catalog/README.md): edit book metadata, fence stale product
  completion and repair an escaped older provider write.
- [Print orders](orders/README.md): retain an accepted-but-unknown occurrence;
  observe its receipt without submitting another physical order.
- [Procrastinate comparison](comparison/README.md): the same immutable-book
  provider/outcomes under transactional rollback and actual process death.

Run `DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django due_check`
to check the local registration, task imports and Beat routes before starting.
See [operations](../docs/operations.md) for worker recovery freshness evidence.

## Adding an example

Keep application code here and import that same code from tests. Use Due's public
API and enroll capabilities in its conformance suite. Each application README
must explain its business outcome, setup, failure controls and observable result.
Keep UI/provider dependencies out of Due's runtime dependencies.
