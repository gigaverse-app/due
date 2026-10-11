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
| Wait until due | Workflow waits for a timeout or changed deadline | PostgreSQL stores the due time; worker/recovery claims eligible work |
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
