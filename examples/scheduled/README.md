# Scheduled publication: change a deadline without losing the obligation

Schedule a text publication, move its deadline earlier or later, or cancel before
provider entry. A Celery worker writes an immutable file when the deadline is due;
Due settles the product's published pointer. Beat recovers overdue work even when
the original notification was lost or the worker restarted.

## Provenance and comparison

This independently written application adapts the movable-deadline scenario from
Temporal's official Python **updatable timer** sample:

- [Pinned source: ff0f1343822e532a5c377f02003002d2dcac4f79](https://github.com/temporalio/samples-python/tree/ff0f1343822e532a5c377f02003002d2dcac4f79/updatable_timer)
- [Current upstream sample and its run instructions](https://github.com/temporalio/samples-python/tree/main/updatable_timer)

| Scenario | Temporal sample | Due application |
| --- | --- | --- |
| Start a deadline | Start a workflow with a wake-up time | `create_publication()` commits product and scheduled work together |
| Move the deadline | Signal the running workflow to update its timer | `reschedule_publication()` replaces the pending desired revision |
| Wait until due | Workflow waits for a timeout or changed deadline | PostgreSQL owns the due time; short-lived timer hints wake the normal fenced claim, with Beat as fallback |
| Completion | Timer finishes | Real file bytes and the product's published pointer become observable |

The original demonstrates a timer, not a publication service. File publication,
UI/CLI, cancellation outcomes and failure tests are additions here. There is no
copied Temporal workflow code or Temporal runtime dependency. This is a scenario
adaptation, **not evidence of feature parity, lower latency, or higher throughput**.
Temporal's workflow history/replay model and broader orchestration capabilities
are outside this comparison. Due uses the existing Django/PostgreSQL application
transaction, plus Redis, Celery and Beat in this setup.

## Run

From the repository root, follow the shared [quickstart](../../README.md#run-the-example):

```bash
uv sync --locked
docker compose up -d --wait
export DJANGO_SETTINGS_MODULE=examples.settings
uv run python -m django migrate
uv run python -m django due_check
```

In three terminals with the same environment, start:

```bash
uv run celery -A examples.celery_app:app worker --loglevel=INFO
uv run celery -A examples.celery_app:app beat --loglevel=INFO
uv run python -m django runserver
```

Open <http://127.0.0.1:8000/scheduled/>. The browser accepts UTC times. Schedule a
minute ahead, move the deadline, refresh to see status, and inspect the published
content. Stop the worker before the deadline and restart it afterward; Beat should
recover publication. Repeat with a cancelled publication: no file should appear.
This is the shared local demo server, without production authentication.

The CLI takes ISO-8601 times with explicit offsets; substitute a future date:

```bash
uv run python -m django scheduled_publication create 'Next edition' --at 2030-01-01T12:00:00+00:00
uv run python -m django scheduled_publication reschedule 1 --at 2030-01-01T11:00:00+00:00
uv run python -m django scheduled_publication show 1
uv run python -m django scheduled_publication cancel 1
uv run python -m django scheduled_publication retry 1
```

`already_started` means cancellation/rescheduling cannot be promised after
provider entry, including an unresolved attempt or a completed publication.
Refused changes leave the product deadline/status untouched. `retry` rearms an
exhausted obligation; it does not create a new edition. Rescheduling a cancelled
publication explicitly schedules it again. Text is immutable after creation.

The provider writes to `DUE_OUTPUT_DIR` (default `/tmp/due-documents`). Use the same
persistent directory for web and worker; a multi-host deployment needs shared
storage or a different provider. Revision keys make replay of the same content
harmless. This is not a claim of exactly-once provider execution. Scheduled work
retains evidence indefinitely (`retention=None`); cleanup policy is outside this
demo. Delivery can be late under worker outages or load; it must not be early.

## Executable evidence

```bash
uv run pytest tests/examples/test_scheduled.py tests/conformance/test_scheduled_contract.py tests/integration/test_scheduled_live.py
```

- Product histories verify earlier/later deadlines, no early publication,
  obsolete deliveries, cancellation, refused changes after provider entry, and
  browser/CLI behavior against actual file bytes.
- Generated conformance runs admission/rollback, lost notifications, retry and
  exhaustion, harmless replay, stale snapshots, and cancellation after provider
  entry with both completion and simulated worker death.
- A real Redis/Celery worker restarts; a separate Beat process recovers a lost
  notification. The scheduled file appears and the cancelled file stays absent.
- CI runs these in both Django dependency lanes and requires executed profiles
  A/B/E/H/I/J plus the complete application effect report. Missing or filtered
  proofs fail the gate. No new unassessed profile debt is accepted for this demo.

## How complete is the port?

The movable-deadline journey is runnable and tested. This is **not a drop-in port
of every upstream command or of Temporal's execution model**:

| Upstream behavior | Due equivalent or difference |
| --- | --- |
| `worker.py` executes the timer workflow | Celery worker executes `PublishAtDeadline`; Beat finds due rows |
| `starter.py` starts tomorrow's timer | `scheduled_publication create --at …` accepts any timezone-aware deadline |
| Re-running starter while its fixed workflow ID is running | Reuse the same submission key to return the existing publication; a fresh key intentionally starts a separate one |
| `wake_up_time_updater.py` signals ten seconds from now | `reschedule ID --at …` changes pending desired state synchronously |
| `get_wake_up_time` workflow query | `show ID` / the detail page reads the committed product deadline |
| Timer returns when its current deadline passes | Due additionally writes a file and settles a product pointer |
| Temporal workflow history, replay and operator UI | No equivalent history/replay UI is provided by this demo |
| Cancellation | Due has an explicit product command; the upstream sample requires Temporal's generic SDK/CLI cancellation |

The upstream does not publish a file, so it cannot demonstrate cancellation after
publication starts. Due's `already_started` boundary is an explicit application
contract, not evidence that Temporal cannot protect an external operation.
Likewise, successful restart recovery is a shared capability, not a Due-only win.

## Run the original next to Due

Install the [Temporal CLI](https://github.com/temporalio/cli/releases/tag/v1.9.1),
then start its local development server in another terminal:

```bash
temporal server start-dev --db-filename /tmp/temporal-timers.db
```

Check out the pinned source in a separate directory:

```bash
git clone https://github.com/temporalio/samples-python.git temporal-samples
cd temporal-samples
git checkout ff0f1343822e532a5c377f02003002d2dcac4f79
uv sync --no-dev
uv run --no-sync updatable_timer/worker.py
```

From another terminal in that checkout:

```bash
uv run --no-sync updatable_timer/starter.py
uv run --no-sync updatable_timer/wake_up_time_updater.py
```

The timer starts a day ahead; each updater invocation moves it to ten seconds
from now. Inspect the workflow and signal/timer events in the native Temporal UI
at <http://localhost:8233>. Due's corresponding controls are at
<http://127.0.0.1:8000/scheduled/>. These are different interfaces: Temporal's UI
is an engine operator console; Due's is a minimal product page.

The [comparison evidence](https://github.com/gigaverse-app/due/pull/19) contains
actual screenshots of both interfaces, original CLI logs, workflow history,
raw timing samples, methodology, and the runner used for the live assertions.
It is an evidence-only PR, deliberately closed without merging run artifacts.
The automated runner imports the pinned upstream workflow unchanged and uses a
real Temporal dev server, PostgreSQL, Redis, Celery and Beat. Temporal is an
optional comparison dependency; it is not needed to run Due's demo or CI.

## What this comparison teaches

Due is a useful fit when the obligation belongs to an existing Django product
transaction. Creating/changing the product and its owed work commits in the same
PostgreSQL transaction. A remote Temporal start/signal call cannot join that local
transaction: an equivalent product integration needs its own reliable handoff,
such as an outbox. The upstream timer has no product DB, so it does not exercise
that problem at all. This is an architectural fit advantage, not a measured
performance advantage.

Due also provides application-bound generated recovery/replay/cancellation proofs
and fences product settlement by the current revision. Those are useful tools
for the BE. Temporal supplies durable workflow history and excellent testing
facilities of its own, including [time skipping and history replay](https://docs.temporal.io/develop/python/best-practices/testing-suite).
Our larger test count does not establish a reliability ranking.

Temporal's timer abstraction is smaller for the original timer-only problem.
Its native UI exposes signals, timers, history and worker information which our
page does not. Due avoids a new workflow service for a Django/Celery deployment,
but it still uses PostgreSQL, Redis, a worker and Beat here. Do not claim fewer
components universally, lower operational cost, or equivalent orchestration.

### Improvements to prioritize

| Priority | Gap | Improvement and acceptance proof |
| --- | --- | --- |
| Delivered | Polling delayed future deadlines | Ten-second lookahead sends transient Celery ETA hints; the five-second recovery cadence remains unchanged. Lost/obsolete hints are tested through the real task and recovery paths |
| Delivered | Retried creates could make separate publications | Reuse `submission_key` for one original request; conflicting input is rejected, including concurrent callers |
| 2 | Product status alone does not explain a late or failed publication | Surface desired/applied revisions, last attempt/error, next recovery and cancellation refusal in an operator view; make the incident/retry journey executable |
| 2 | `retention=None` grows retained scheduled evidence | Design safe archival/retention that preserves stale-delivery and unresolved-attempt protection; benchmark selection with retained history before allowing pruning |
| 2 | Local files assume shared storage, and the browser needs manual refresh | Provide a shared/object-storage example and clearer live status/actions; keep its provider guarantees explicit |
| 3 | A timer sample cannot assess complex orchestration or production capacity | Add matched external-effect workloads and controlled load/resource measurements; compare multi-step workflows separately |

The timing runner reports command acknowledgements separately from observed
lateness after the final deadline. Due's observation includes file publication
and DB settlement; Temporal's includes only timer/workflow completion. Small,
sequential local runs can reveal polling latency, but cannot establish production
throughput, CPU/memory efficiency, cost, or comparative failure rates. Treat
arrival phase, persistence configuration and observation interval as part of the
measurement, not incidental details.

## Deadline hints and retry-safe submission

See the [before/after evidence](https://github.com/gigaverse-app/due/blob/54aba8e3ba93ef100578ec8ffa5897d4c95071c8/evidence/scheduled-temporal/deadline-improvement/README.md) for latency, SQL write/row and broker-message accounting, plus fresh screenshots.

`PublishAtDeadline.deadline_wakeup_horizon = timedelta(seconds=10)` opts into
Due's shared scheduled transport. A post-commit nudge publishes an ETA hint when
eligibility is within ten seconds. Recovery also looks ahead, sending at most
`policy.batch_size` hints per pass in earliest-deadline order. Long deadlines
stay solely in PostgreSQL until they enter that window. Other scheduled adopters
retain their current behavior unless they opt in; the supported horizon is
positive and at most five minutes, and requires a registered Celery route.
Choose a horizon greater than the recovery cadence if every future deadline
should normally be discovered before it becomes due.

Hints contain the row ID, desired revision and eligibility timestamp. They never
reserve work early, change retry backoff, or grant authority to publish. Delivery
rechecks PostgreSQL time and then uses the ordinary claim/fenced worker path.
Cancellation or replacement invalidates an old hint; duplicates are harmless.
A lost hint, early delivery, broker outage or page overflow can increase lateness,
but the existing due-work sweep still owns eventual progress. This is not a
hard real-time guarantee. Worker/broker capacity must accommodate the short ETA
window; repeated sweeps can send duplicate hints, and rapid rescheduling leaves
obsolete hints until delivery. There is no persisted “hint sent” marker.

**SQL cost:** hints add reads and broker messages, but no SQL rows or writes.
Successful hint execution uses the existing claim, attempt and settlement writes.
No additional deduplication table is introduced: `submission_key` (unique UUID)
and `initial_publish_at` live on the publication row. Retrying an already committed
submission does a lookup without creating another publication, work row or hint.
The schema migration backfills existing publication rows once.

The browser puts a new UUID in each creation form and preserves it on repost.
The CLI and Python API accept an explicit key:

```bash
uv run python -m django scheduled_publication create "One edition" \
  --at 2030-01-01T12:00:00+00:00 \
  --submission-key 70525b27-3d85-456c-9a21-f2f1e6b1d518
```

Reuse that exact key, text and original deadline after a timeout. The response is
the existing publication in its current state, even after rescheduling,
cancellation or completion. Reusing a key with different original input raises
`SubmissionConflict` (HTTP 409 in the browser). A fresh form or omitted API/CLI
key intentionally creates a new publication. Keys are global to this demo;
a multi-tenant adopter should scope its unique constraint and lookup to its
canonical tenant identity. Keep the row for as long as the retry promise lasts;
deleting it also deletes that promise. The demo already retains scheduled rows.

See [migration findings and proof boundaries](../../docs/demo-comparisons.md) for
the cross-demo comparison and independently declared process outcomes.
