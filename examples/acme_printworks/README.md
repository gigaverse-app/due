# Acme Printworks: Django Tasks to native Due

Runnable companion to the DEV tutorial by the Due project at Gigaverse, prepared
and verified with OpenAI Codex. Fictional company and provider contracts.

## Download and run

Clone the Due repository and enter this example:

```bash
git clone https://github.com/gigaverse-app/due.git
cd due/examples/acme_printworks
```

Requirements: Python 3.14, Docker Compose, Linux/macOS or Windows with WSL.

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
docker compose up -d --wait
python run_demo.py
python -m pytest -q -p due.django.conformance.reporting --due-work-require-assessed --due-work-profile-report=profiles.json --durable-work-report=effects.json
```

`run_demo.py` applies the included migrations, starts separate Celery worker and
Beat processes, runs the scenario, and stops those processes in a `finally` block.
It creates a fresh job each run, starting at 418. Logs are `worker.log` and
`beat.log`. The deliberate lost printer reply appears as an error in the worker
log before observation recovers it. This is expected.

Stop the tutorial services with `docker compose down` when finished. The provided
Compose services are disposable local development services; no persistent volumes
are configured. The Python provider simulator stores its records in
`providers.sqlite3` in the project directory.

If using existing **dedicated development** services, set `PGHOST`, `PGPORT`,
`PGDATABASE`, `PGUSER`, `PGPASSWORD`, and `CELERY_BROKER_URL` instead of starting
Compose. Defaults: PostgreSQL localhost:55451, database/user/password `acme`;
Redis localhost:56399/0. Tests require permission to create a test database.

## What runs

1. Standard `@task` counts immutable revision 17. Its fast notification is lost;
   Beat recovers the committed obligation and the real worker stores 4 words.
2. Proof publication moves from revision 17 (June 12) to revision 18 (June 19).
   Each desired revision has immutable output; only current work can publish.
3. A separately committed approval portal receives the published proof.
4. The printer accepts 500 copies but loses the response. Due observes the
   correlated receipt and records acceptance; provider order count remains one.
5. Rendering reserves independent cleanup before allocating temporary output.
   Ordinary execution cleans immediately; a process-death test exercises fallback.
6. Always build the application contract. Four native capability contracts generate
   270 tests from real application bindings and declared policies. Three shared
   replay proofs also exercise the initial word-count task. Enrollment guards
   reject a deleted suite; CI requires every expected proof to execute and pass.

`acme/stage2.py` is the early ReplaySafeEffect implementation, exercised by its own
test. The running app registers the final implementation in `acme/effects.py`.
The word-count task stays a Django task throughout. All models and migrations are
included. The application exposes service functions and management commands; a web
UI, authentication and billing are outside this tutorial.

The story's shared clock starts r17 at minute 0, accepts the corrected r18 at
minute 2, and publishes its faster render at minute 3. Without a restart, r17
returns late at minute 10. In the alternate history, a restart kills it at
minute 7; it cannot also finish at minute 10. Due preserves r18 and recovers
independently owed cleanup after its reservation expires. Any still-current,
unfinished proof needs a new attempt after lease expiry, not a resumed PDF process.

Both proof implementations use `acme/rendering.py`: an 11-minute soft execution
limit, 12-minute hard limit, and 15-minute lease. A claim at minute 0 without
renewal expires at minute 15; the five-second recovery cadence is not a guarantee
of immediate recovery at minute 7. Bound a real provider call below the soft
limit. The text simulator remains fast; tests compress the relevant histories.

## Provider contracts

The proof is a text preview rather than a PDF, keeping the example dependency-light.
The simulator uses real SQLite transactions outside Django's PostgreSQL transaction.
No paid provider, external account, or API key is needed. It deliberately models:

- Immutable proof keys; equal retries are accepted, conflicting bytes rejected.
- An unconditional portal PUT, allowing a late older write to overwrite newer data.
- A printer where **every POST creates an order**. The reference is searchable,
  not a deduplication key. Observation checks reference, proof and quantity.
- A renderer with a terminal completion record. Once finished, that attempt cannot
  write again. Cleanup only reports success with that evidence; absence is inconclusive.

A real provider adapter must establish these contracts. In particular, a database
fence cannot cancel a remote call, and a timeout cannot prove that a print order
was rejected. Acceptance also does not prove that the brochures were printed or shipped.
Revisions and approved print requests are immutable through the sample's service
API. Production admin/API write paths must preserve that rule. Each approval call
creates a distinct purchase intent; add request deduplication at your HTTP boundary
before exposing the command to repeated browser submissions.

## Contracts and verification

Read the [complete tutorial](../../docs/acme-printworks-tutorial.md), first
[published on DEV](https://dev.to/aviadr1/django-tasks-with-due-commit-the-work-recover-after-a-crash-n5k).

Start with [tests/test_task_contract.py](tests/test_task_contract.py) for the
Django task. [tests/test_contracts.py](tests/test_contracts.py) enrolls the native
capabilities; [tests/contracts](tests/contracts) supplies their application meaning.
The full suite has 286 tests: 270 generated native checks, three shared task replay
proofs, ten scenario tests, one rendering-budget check, and two enrollment guards. The generated suites leave
no applicable requirement unassessed. Non-applicable families have explicit reasons.
This is evidence for these bindings and provider assumptions, not a guarantee
about an arbitrary real printer or PDF renderer.

From the repository root, the CI runner checks migrations, all contracts, complete
executed reports, and the real worker/Beat demo against the checkout:

```bash
DUE_DATABASE_URL=postgresql://acme:acme@localhost:55451/acme \
DUE_BROKER_URL=redis://localhost:56399/0 \
python scripts/check_acme_printworks.py
```

Use an environment with the repository's development dependencies for that command
(`uv sync --all-packages`). It creates and removes its own uniquely named databases,
queue and provider files. Reports go to ignored `acme-reports/`.

The standalone requirements pin Python-compatible Django 6.0.9, published
`django-due==0.8.0` and `pytest-obligation==0.15.0`. Verification used Python 3.14,
PostgreSQL 16, Redis 7, Celery prefork workers and Beat. Tests control clocks and
transport faults while running Due's real transitions. The live demo verifies
actual broker and scheduler wiring. The provided Compose launcher is a convenience;
local verification used equivalent dedicated PostgreSQL/Redis services.
