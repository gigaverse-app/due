# Widget Store

A runnable storefront for inventory reservations, payment messages, compensation and resumable fulfillment. It adds capabilities absent from the books and scheduled-publication demos: two customers racing for scarce stock, a durable wait for a business decision, atomic reservation admission, and progress across multiple committed steps.

## Run

From the repository root (Python 3.14, PostgreSQL and Redis):

```sh
uv sync --all-packages --locked
docker compose up -d
export DJANGO_SETTINGS_MODULE=examples.widget_store.settings
uv run python -m django migrate
uv run python -m examples.widget_store.supervisor
```

Open http://127.0.0.1:8000/widgets/. Reserve a widget, choose simulated success or failure, and use **Crash & restart application** during dispatch. The POSIX supervisor kills the web process, Celery worker including its children, and Beat with SIGKILL, then restarts them. PostgreSQL and Redis stay up. Recovery waits for the dead worker's 20-second lease to expire, then a one-second retry delay. The ordinary unsupervised Django server disables the crash endpoint. This is a local demo, with CSRF protection but no user authentication; do not expose it publicly.

Failed payments and unpaid reservations (60 seconds) restore inventory once. A conflicting or late payment is rejected; resending the same accepted decision is harmless. Retrying checkout with the same UUID returns its original order. The browser retains its UUID if the checkout response is lost. Restock resets **available** inventory to 100, matching upstream; it is an operator action, not an inventory conservation experiment.

## Provenance and comparison scope

The scenario is [DBOS's Python Widget Store](https://github.com/dbos-inc/dbos-demo-apps/tree/ddaf50e71fa8e19364d9c70b5d87a9c8203af152/python/widget-store), commit `ddaf50e71fa8e19364d9c70b5d87a9c8203af152`, DBOS `3.2.0`. See the [original walkthrough](https://docs.dbos.dev/python/examples/widget-store). Due's application and UI are independently written. The comparison runner checks out the unmodified original and uses its lockfile; no upstream source or assets are vendored.

Both apps provide inventory, checkout, simulated payment success/failure, order progress, restocking and an actual crash button. Both perform ten one-second dispatch steps. This is scenario parity, not wire-API compatibility: Due returns JSON admission receipts, DBOS waits for workflow events. The benchmark therefore reports HTTP receipt time separately from observable business completion. Due additionally rejects contradictory/late payment decisions explicitly.

Due stores one obligation per accepted order, reusing the order's progress fields rather than inserting a row for each stage. Reservation and obligation admission commit together. Each PostgreSQL transition identifies its exact stage and locks its order; the guard and progress update share a transaction. Stock restoration and cancellation share a transaction too. No external charge or shipment occurs: PostgreSQL transaction atomicity does **not** establish exactly-once effects at a real payment provider.

A current tradeoff: Due occupies one worker slot for the ten-second dispatch, and the pinned DBOS example also uses synchronous sleeps, with DBOS recording their deadlines. The payment wait consumes no Due worker slot. This demo deliberately does not introduce a second workflow engine or a custom recovery loop; longer waits should use scheduled capabilities. Benchmark results are bounded workflow latency and database-write observations, not capacity claims.

## Proofs

`integration_tests/conformance/widget_examples.py` binds the real commands to `effect_contract_suite` and pytest-obligation. It requires declared product outcomes, payment gating, admission rollback, bounded retries and visible operator recovery. The replay binding repeats the actual final-stage database operation; complete-workflow process histories cover committed payment, mid-dispatch and compensation boundaries. Negative controls corrupt dispatch, disable recovery and suppress compensation, and must be rejected by those same proofs.

```sh
uv run pytest integration_tests/examples/test_widget_store.py integration_tests/conformance/test_widget_contract.py integration_tests/conformance/test_widget_controls.py
```

The normal suite uses zero-duration stages for deterministic functional checks; real browser and comparison runs keep all ten one-second stages. CI explicitly requires the generated checkout contract and both process histories so deleting enrollment cannot produce a green empty report.
