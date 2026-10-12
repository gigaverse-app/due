# Beat spin and serial worker pool recovery

## Archaeology and invariants

Celery introduced the 10 ms advance in
[20340d79b (2014)](https://github.com/celery/celery/commit/20340d79b55137643d5ac0df063614075385daaa)
to compensate for timer lateness. Its
[changelog](https://github.com/celery/celery/blob/v5.6.3/docs/history/changelog-3.1.rst)
explicitly discusses cumulative drift. Preserve the early check, heap ordering,
due predicate, schedule persistence and immediate draining of due work. Repair
only the negative *sleep interval* which otherwise spins in Service.start().
Changing adjust() itself failed the neighboring-deadline test: a later entry
overtook an earlier one inside the 10 ms window.

Due's separate transient hints originated in eaa043f and were shared with
continuations in #34. They remain unchanged: PostgreSQL's due predicate and
fencing authorize effects, and periodic recovery survives hint loss.

Psycopg introduced failed-check backoff in
[0d1cd270 (2023)](https://github.com/psycopg/psycopg/commit/0d1cd270732101312111e15b00975a5669b214d6)
to stop busy retries and honor acquisition timeouts (#709). Do not disable
health checks, shorten outage backoff or replay transactions. The widget demo
enabled checks in ff0011c, then added proper task connection boundaries in #41.
A cold serial checkout with min_size=1/max_size=4 grows two connections; killing
both causes two failed checks and the second starts approximately one second
of backoff. Give each serial prefork child max_size=1; retain max_size=4 for
threaded web requests. This is a deployment configuration fix, not a general
single-connection recommendation for concurrent workers.

## Proof

- Deterministic real PersistentScheduler/memory-broker journeys: interval and
  cron deadlines, intentional early check, no early publication, bounded drift,
  bounded tick count; unmodified Celery is the no-spin negative control.
- Neighboring deadlines retain heap order; default binding selects the scheduler
  while explicit application scheduler extensions remain untouched.
- Real PostgreSQL worker pool: terminate its exact owned PID and acquire a
  healthy replacement, without spare stale sockets.
- Run the generated pytest-obligation widget reconnection/replay contract with
  the production worker pool. Turning off socket validation must fail it.
- Existing replay, process crash, hint-loss, progress and compensation negative
  controls remain mandatory in CI.
- Compare exact pre-fix 1f15624 with candidate against the same pinned DBOS app,
  both execution orders, then separate repeated CPU and wall profiles. SQL row
  budgets and all application outcomes remain unchanged. WSL probes diagnose
  mechanisms; CI supplies reported application measurements.
