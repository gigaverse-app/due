# Operating Due

Every process must install the same registrations. Run a Celery worker on each
registered queue and recurring recovery at each declared cadence. Recovery is
required for correctness after a lost wakeup. Keep provider timeouts below the
worker soft limit, the soft limit below the hard limit, and the hard limit below
the lease. [Registration and startup](integration.md).

## Check configuration and recovery freshness

```bash
uv run python -m django due_check
uv run python -m django due_check --metrics-file /tmp/recovery-workers.prom
```

`due_check` checks registration coverage, actual Celery imports and both the
Django and Celery views of each recovery schedule, including cadence, queue and
expiry. It exits nonzero on drift. It never repairs configuration silently.

Without `--metrics-file`, it explicitly reports recovery freshness as NOT CHECKED.
Supply a fresh Prometheus text scrape from the recovery workers to check every
registered effect's last clean tick against its runtime-declared stale threshold.
Missing, stale or future-dated evidence fails. A management process's own metrics
cannot establish another worker's health. This remains a point-in-time check;
keep the continuous alerts described below and monitor exporter availability.

## Inspect stopped or uncertain work

```bash
# Under your application's DJANGO_SETTINGS_MODULE:
uv run python -m django due_status
uv run python -m django due_status publish_document --limit 10
```

The command emits one JSON object per registered capability. Each includes:

- `rows`: the oldest EXHAUSTED, TERMINAL_FAILED and UNKNOWN obligations;
- `unknown_attempts`: unresolved provider attempts and repair status;
- `recovery_deferrals`: identities held out of a recovery phase, failure counts
  and retry times.

`--limit` bounds each list independently (default 20, maximum 1,000). Capability
names must match the installed registry. The command is read-only: it does not
rearm work, clear holds, publish messages or contact providers. It omits captured
payloads, receipts and raw exception strings. Application identifiers and tenant
keys remain visible, so run it with the application's normal operator access.

UNKNOWN is a request for evidence, not a failed call that is automatically safe
to retry. Use the provider's correlated receipt or reconciliation API. An
EXHAUSTED obligation needs an explicit authorized decision; an identical producer
request does not reset its budget. Application runbooks own that decision.

## Export and alert on recovery

Due uses `prometheus_client`; the application owns its metrics endpoint and
multiprocess configuration. Workers that publish metrics must be included in the
exporter's registry. Sentry reporting is active only when the application
configures the SDK. Metrics failures do not roll back product truth.

| Metric | What to watch |
| --- | --- |
| `due_backlog{effect,state}` | Growing work, including stopped and unknown states |
| `due_oldest_age_seconds{effect,state}` | Age of the oldest outstanding/stopped work |
| `due_pending_provider_attempts{effect,outcome}` | Evidence still needing reconciliation |
| `due_last_recovery_timestamp_seconds{effect}` | Last clean recovery tick |
| `due_recovery_stale_after_seconds{effect}` | Runtime-declared heartbeat tolerance |
| `due_recovery_failures_total{effect,phase}` | Per-identity or phase failures |
| `due_recovery_deferred{effect,phase}` | Identities temporarily held out with backoff |
| `due_recovery_deferred_oldest_age_seconds{effect,phase}` | Age since the oldest held identity first failed |

A useful stale-tick expression, after metrics have been initialized, is:

```promql
time() - due_last_recovery_timestamp_seconds > due_recovery_stale_after_seconds
```

Also monitor missing series and exporter availability. A never-started worker
cannot report its own failure. Choose backlog and unknown-age thresholds from the
application's completion expectations.

A clean tick does not imply all identities are healthy: poisoned work may be held
out by backoff. Alert on the **age of deferred identities**, not only recent
failure counters. Backoff eventually spaces failures an hour apart, while the
obligation remains owed. [Regression evidence](../tests/capabilities/test_recovery_deferral.py).

## When recovery aborts instead of isolating a row

`due_recovery_passes_total{effect,outcome="aborted"}` means the tick stopped early.
A soft worker limit, unusable transaction, missing Due wiring or an application's
configured fatal error can cause this. Check process configuration before treating
it as another poisoned identity. Failed and aborted ticks do not advance the clean
heartbeat; neither outcome authorizes replay of UNKNOWN work.

[Configure fatal application errors](integration.md#a-bad-row-is-different-from-a-broken-process)
once in each process. For a genuinely bad identity, ordinary isolation still
allows other rows to progress and exposes the deferred identity for diagnosis.

## Database-backed Beat

The optional `due[beat]` extra supports `django-celery-beat`.
`remove_stale_recovery_schedules(registrations)` removes only unowned entries
whose task is Due's exact recovery task. Call it intentionally during scheduler
startup; it never sweeps unrelated tasks or old backend namespaces. Applications
migrating from the original primitive must explicitly retire their legacy task
routes after the data and worker cutover.

## Failed infrequent recovery

`due_last_unclean_recovery_timestamp_seconds{effect=...}` records the end of a
failed or aborted heartbeat tick, using the worker's wall clock even when the
database connection failed. Clean ticks advance
`due_last_recovery_timestamp_seconds`; they leave the failure stamp unchanged.
A fast-path loop with `heartbeat=False` changes neither timestamp.

For infrequent sweeps, compare the latest unclean timestamp with the latest clean
heartbeat instead of relying only on a short-window counter increase. Handle an
absent clean series too, since the first ever run may fail. For example:

```promql
(due_last_unclean_recovery_timestamp_seconds
 > on(effect) due_last_recovery_timestamp_seconds)
or
(due_last_unclean_recovery_timestamp_seconds
 unless on(effect) due_last_recovery_timestamp_seconds)
```

Use matching service/environment labels when combining multiple applications.
Keep worker and database clocks synchronized: close clean/unclean outcomes can
otherwise appear reordered. Choose alert routing and cadence policy in the application. This package exports
the observation; installing it does not migrate a backend's dashboards or alerts.
