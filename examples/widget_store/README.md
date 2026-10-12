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

Open http://127.0.0.1:8000/widgets/. Reserve a widget, choose simulated success or failure, and use **Crash & restart application** during dispatch. The POSIX supervisor kills the web process, Celery worker including its children, and Beat with SIGKILL, then restarts them. PostgreSQL and Redis stay up. If a worker dies inside a claimed step, recovery waits for its 20-second lease and one-second retry delay. Between stages the claim is released, so restart resumes at the next due recovery tick. The ordinary unsupervised Django server disables the crash endpoint. This is a local demo, with CSRF protection but no user authentication; do not expose it publicly.

Failed payments and unpaid reservations (60 seconds) restore inventory once. A conflicting or late payment is rejected; resending the same accepted decision is harmless. Retrying checkout with the same UUID returns its original order. The browser retains its UUID if the checkout response is lost. Restock resets **available** inventory to 100, matching upstream; it is an operator action, not an inventory conservation experiment.

## Provenance and comparison scope

The scenario is [DBOS's Python Widget Store](https://github.com/dbos-inc/dbos-demo-apps/tree/ddaf50e71fa8e19364d9c70b5d87a9c8203af152/python/widget-store), commit `ddaf50e71fa8e19364d9c70b5d87a9c8203af152`, DBOS `3.2.0`. See the [original walkthrough](https://docs.dbos.dev/python/examples/widget-store). Due's application and UI are independently written. The comparison runner checks out the unmodified original and uses its lockfile; no upstream source or assets are vendored.

Both apps provide inventory, checkout, simulated payment success/failure, order progress, restocking and an actual crash button. Both perform ten one-second dispatch steps. This is scenario parity, not wire-API compatibility: Due returns JSON admission receipts, DBOS waits for workflow events. The benchmark therefore reports HTTP receipt time separately from observable business completion. Due additionally rejects contradictory/late payment decisions explicitly.

Due stores one obligation per accepted order, reusing the order's progress fields rather than inserting a row for each stage. Reservation and obligation admission commit together. `DatabaseEffect.transition` commits each product transition and its next deadline together under the work fence. The engine owns locking, rollback and duplicate-delivery protection; the app owns inventory, payment and dispatch decisions. Stock restoration and cancellation share that transaction too. No external charge or shipment occurs: PostgreSQL transaction atomicity does **not** establish exactly-once effects at a real payment provider.

Due uses `ContinueAfter` to release its claim and worker slot between dispatch stages. A persisted deadline and the registered recovery sweep resume the same obligation; no asyncio event loop or per-stage obligation row is introduced. A bounded transient Celery ETA hint wakes each committed stage without waiting for Beat. Hints share the scheduled-publication library mechanism and add no SQL rows or receipts. Losing every hint falls back to the same sweep; early, duplicate or obsolete hints cannot bypass the database deadline, eligibility or claim. Each stage still pays the ordinary claim/continuation update cycle, and fallback after hint loss can wait for a recovery tick. The pinned DBOS example uses synchronous `DBOS.sleep`, which records sleep deadlines. These costs are measured, not hidden by reducing the one-second business waits. Benchmark results are bounded workflow latency and database-write observations, not capacity claims.

## Proofs

`integration_tests/conformance/widget_examples.py` binds the real commands to `effect_contract_suite` and pytest-obligation. It requires declared product outcomes, payment gating, admission rollback, bounded retries and visible operator recovery. The ordinary delivery binding proves the first payment-decision stage; the distinct delivery-replay binding repeats the same final-stage message and requires one actual database callback. This does not claim that a raw intermediate callback is independently idempotent. Complete-workflow process histories cover committed payment, writes before decision/dispatch commit, committed continuations and compensation boundaries. Named completion contracts require the final product outcome in 10–12 simulated seconds with hints and with every hint lost, keeping the actual one-second business waits. Negative controls corrupt dispatch, disable recovery, suppress compensation, shorten waits and reverse progress, and must be rejected by those same proofs.

```sh
uv run pytest integration_tests/examples/test_widget_store.py integration_tests/conformance/test_widget_contract.py integration_tests/conformance/test_widget_controls.py
```

The normal suite uses zero-duration stages for deterministic functional checks; real browser and comparison runs keep all ten one-second stages. CI explicitly requires the generated checkout contract and all four process histories (paid/failed, with/without timer hints) so deleting enrollment cannot produce a green empty report.

## Reproduce the comparison

Use two disposable databases on the same PostgreSQL server. Install both lockfiles under the same Python version, migrate each, then run the shared controller:

```sh
git clone https://github.com/dbos-inc/dbos-demo-apps /tmp/widget-upstream
git -C /tmp/widget-upstream checkout ddaf50e71fa8e19364d9c70b5d87a9c8203af152
uv sync --directory /tmp/widget-upstream/python/widget-store --locked --python 3.14
export DUE_DATABASE_URL=postgresql://due:due@127.0.0.1:55439/widget_due
export DBOS_DATABASE_URL=postgresql+psycopg://due:due@127.0.0.1:55439/widget_dbos
export DUE_BROKER_URL=redis://127.0.0.1:56389/0
# Create widget_due and widget_dbos first, with the due role.
uv run python -m django migrate --noinput
uv run --directory /tmp/widget-upstream/python/widget-store alembic upgrade head
uv run --directory /tmp/widget-upstream/python/widget-store python -m migrations.reset_database
uv run playwright install chromium
uv run python -m scripts.widget_benchmark --upstream /tmp/widget-upstream --samples 5 --browser
```

The runner binds ports 8000 and 8001, owns and cleans up both supervisors, excludes one equivalent warmup per arm, retries each submission once, and verifies terminal status, progress and inventory. It separately measures checkout acknowledgement, payment acknowledgement, payment-to-decision and payment-to-terminal. A crash happens after three committed stages. PostgreSQL table counters report inserts/updates/deletes across the measured window, including recovery metadata and a two-second statistics tail; these are physical table-write observations, not SQL query counts. A `--reverse` run reverses arm order to check ordering effects. Do not run other clients against these databases during measurement.

Timing is descriptive (small sample, runner-dependent), with no flaky faster-than threshold. Correctness failures fail CI. Chromium screenshots and raw JSON/logs are uploaded by the **Widget Store comparison** workflow; run-specific evidence stays in artifacts, not the package. The browser journeys run after timing and therefore do not inflate the measured write counts. Default settings use only the widget recovery registration; unrelated example recovery sweeps are excluded from the comparison.

## Database cost and authoring

The original transient-hint comparison recorded **14 inserts for Due versus 339
for DBOS across seven orders: 24.2× fewer inserts (95.9% fewer)**. That is an
advantage in insert volume, not proof of lower total cost: that run also
recorded 357 Due updates versus 110 DBOS updates. See [PR 34's benchmark discussion](https://github.com/gigaverse-app/due/pull/34).

The current runner also records application SQL calls/execution time, buffer
activity, statement WAL, sampled application CPU, PostgreSQL/Redis container CPU,
and broker commands/bytes. It excludes its SQL observer session from statement
statistics. Container CPU still includes observation and housekeeping; the 10ms
process sampler can miss CPU at process exit. Keep these dimensions separate:
less WAL does not imply less CPU or a lower cloud bill. Both arms keep their
original frameworks and full crash/restart costs; DBOS's extra history is useful
checkpoint/debugging data which Due does not recreate.

Enable `pg_stat_statements` in PostgreSQL's `shared_preload_libraries`, restart
that dedicated test server, and enable `track_io_timing`. Set
`WIDGET_POSTGRES_CONTAINER` and `WIDGET_REDIS_CONTAINER` to dedicated container
names for CPU attribution. The CI workflow does this automatically. Results,
statement breakdowns and screenshots stay in CI artifacts and the PR discussion.

The database callback is deliberately small:

```python
class CheckoutOrder(DatabaseEffect[Order, CheckoutWork]):
    def transition(self, work):
        services.advance(work)  # business writes; owner already locked
        if work.owner.status in {Order.Status.CANCELLED, Order.Status.DISPATCHED}:
            return None
        return ContinueAfter(timedelta(seconds=1), until=None)
```

Payment eligibility, atomic admission and exhaustion mirrors remain ordinary Due
bindings. A backend can use the same mode for short database-only transitions;
its provider calls continue to require replay-safe or evidence-aware effects.
There is no new per-stage table, separate application deadline or step-identity
protocol to maintain.

The standalone settings enable Django's supported psycopg pool (one to four
connections per process), pool checkout health checks, and Celery recycling its
pools after 100 tasks; DBOS already pools through SQLAlchemy. Crash testing
still kills every application process, including these pools. The comparison
therefore includes reconnect/startup work after the crash, as well as steady
operation. No durability setting or recovery cadence is relaxed.

A separate, unmeasured correctness journey terminates both apps' live database
sessions during dispatch. Each must reconnect and finish without resetting
progress or changing inventory. This fault is excluded from the cost table.

## CPU and reconnect profiling

The single-worker supervisor embeds one normal Celery Beat child in the prefork
worker. This shares startup imports; it does not change recovery cadence or
disable sweeps. Deployments with multiple workers must run one separate Beat
instance, rather than embedding a scheduler in every worker.

Due's execution, recovery, timer and observation tasks now call Django's
`close_old_connections()` at worker entry and return. With these demo settings,
each task returns its socket to the pool, and checkout health checks discard
terminated sessions before the next delivery. Celery can therefore reuse the
pool longer without retaining a broken connection across tasks. Eager calls
borrow their caller's connection and leave its transaction alone. These hooks
neither retry a task body nor decide whether a lost commit succeeded; the
existing obligation/fence/recovery protocol still owns that decision.

The comparison workflow measures the pinned pre-change Due checkout and the
candidate through the same controller, each against the unmodified DBOS app,
with both arm orders. Per-process CPU and cumulative phase snapshots distinguish
warm order processing from crash/startup work. Two separate CPU captures and one wall-clock capture use
pinned Yappi 1.7.6 in both applications. The opt-in bootstrap covers all threads,
fresh prefork counters and crash restarts; it changes no upstream source or
application configuration. CPU captures use five paid orders; wall captures use
two. Both include failed payment, a crash and SQL-session loss.

Each capture must finish both applications' correctness journeys and contain
profiles for every supervised process before and after the crash. It must show
actual checkout, advance and dispatch calls in each application's corresponding
process generation. Negative controls reject incomplete journeys, missing
processes or business calls, clock mismatches, snapshot errors and corrupt data.
Real-process tests also check that the main thread is not counted twice and a
fork cannot retain counters or overwrite its parent's file.

Yappi's per-thread CPU clock profiles Python functions and builtins. Wall-clock
profiles include waits and overlap across threads. The snapshot writer thread is
excluded. Live snapshots are written every second so SIGKILL cannot discard the
whole profile; the final interval can be missing, scheduling delays can extend
it, and active calls may have incomplete inclusive totals. These profiles locate
hot functions, not exact whole-journey call counts or production CPU percentages.

Profiled runs are labelled and excluded from timing claims. Raw per-process
functions, self/inclusive summaries, source provenance and validation results
remain CI artifacts. A failed capture fails the job. There is no silent retry or
collector fallback. Blocking py-spy was rejected after it stalled DBOS in CI;
nonblocking py-spy produced too many inconsistent reads. See the profiling
findings below for the retained evidence and limitations.

To reproduce a CPU capture against the pinned upstream checkout (after the
database setup above), use a fresh output directory:

```bash
uv run python -m scripts.widget_profile --mode cpu --output test-results/widgets/profile/manual -- --upstream /path/to/dbos-demo-apps --samples 5
```

The generated `delivery-replay-reconnected dispatch` contract terminates the real
PostgreSQL session before each of two deliveries through Celery's production
tracer. It requires the independent dispatched product outcome and one business
callback. Disabling the entry cleanup must fail that same case. The separate
benchmark journey terminates every SQL session in the app's isolated database
while its real prefork worker is dispatching, then checks completion, inventory,
and a subsequent browser checkout.
