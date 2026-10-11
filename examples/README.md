# Runnable applications

Examples live alongside Due so changes to the public API and the applications
that exercise it can be reviewed and tested together. They share
`examples.settings`, `examples.celery_app`, and the root `compose.yaml`.

## Document publication

The [document application](documents/) writes an immutable file and publishes its
key on a Django model. Follow the [root quickstart](../README.md#run-the-example).
Its [worker test](../tests/integration/test_celery_worker.py) drops the initial
notification and checks recovery through PostgreSQL, Redis and a Celery worker.

## Adding an example

Keep application code here and import that same code from tests. Use Due's public
API and enroll capabilities in its conformance suite. Each application README
must explain its business outcome, setup, failure controls and observable result.
Keep UI/provider dependencies out of Due's runtime dependencies.
