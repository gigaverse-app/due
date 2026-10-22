# Contributing to Due

Due uses Python 3.14 and uv. Install Docker (or supply dedicated PostgreSQL and
Redis instances), then:

```bash
uv sync --all-packages --locked
uv run pre-commit install
docker compose up -d --wait
uv run pre-commit run --all-files
(cd package && ../.venv/bin/python -m pytest --due-work-check-fixtures)
uv run --no-sync pytest --due-work-require-assessed --due-work-profile-report=profiles.json
uv run python scripts/check_adoption_report.py profiles.json --contract "Due replay-safe work table" --verified A B D I J
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django makemigrations --check --dry-run
uv build --project package --out-dir dist
```

`uv.lock` is committed. Dependency edits need `uv lock`; normal development and
CI use `--locked`. Pre-commit runs Ruff lint/format on staged Python files and
Pyrefly on the whole project. Hooks can modify formatting; review and stage those
changes before committing again. Database and worker tests run separately from
commit hooks so a commit does not depend on Docker availability.

Tests use real PostgreSQL. `pytest-django` creates and drops its test database;
the configured user must be allowed to create databases. The worker integration
tests also use Redis and separate Celery prefork processes. The live books
journeys additionally start a real Django HTTP server and Celery Beat, verifying
normal form submission and scheduled recovery of either lost notification. Set
`DUE_DATABASE_URL` and `DUE_BROKER_URL` for dedicated test services, or use the defaults in
[compose.yaml](compose.yaml). Stop local services with `docker compose down`.

## Changes and proofs

Keep the public API focused on work tables. The delivery lease in
`runtime/_delivery.py` is private machinery for observation workers; it is not a
public native-table composition API. Use [the capability guide](package/docs/capabilities.md)
to find the existing owner of behavior before adding another abstraction.

For behavioral fixes, first write a failing history at the boundary that broke:
commit, publication, provider call, evidence, or local settlement. Prefer real
transactions and selectors. Fault injection may replace transport or the
provider; it must not replace the production decision being proved. Include a
control that demonstrates the fault was reached, and observe the product result.

## Pull requests and CI

Use Conventional Commit titles (`feat:`, `fix:`, `docs:`, `test:`, `ci:`,
`refactor:`, `build:`, `perf:`, `revert:`, `chore:`), enforced by the PR-title
workflow. Explain the resulting behavior, relevant validation and limitations.

CI has independent lint/workflow validation, Pyrefly, distribution, and
PostgreSQL/Redis test jobs. The test suite runs against locked Django and the
minimum supported Django version, including the live HTTP/worker/Beat journeys.
Obligation reports, process logs and distributions are retained as run artifacts.
The distribution job builds through the sdist, then imports the wheel with
isolated Python and no development dependencies.

`main` requires `lint`, `typecheck`, `tests (locked)`, `tests (minimum)`,
`build` and `conventional-commit` to pass from GitHub Actions, with the branch
up to date before merging. These repository protections apply to administrators
too. Preserve these check names when changing CI, or update branch protection
together with the workflow.

To reproduce the installed-wheel check:

```bash
uv build --project package --out-dir dist
uv venv /tmp/due-wheel
uv pip install --python /tmp/due-wheel dist/*.whl
/tmp/due-wheel/bin/python -I scripts/check_wheel.py
```

Release versions and manual publishing approvals are described in [RELEASING.md](RELEASING.md).

See [package ownership and subtree development](package/docs/development.md) for library edits made inside an adopter checkout.


## Selective CI inputs

`.github/test-lanes.json` selects complete library, integration and widget
comparison lanes. Build, lint and type checks always run. Main and `test:full`
run complete suites; a failed planner also runs the affected workflow in full.

The widget comparison uses the standalone widget app. Its inputs include all
Due runtime/shims, widget code/assets/migrations, shared Django settings and Celery
bootstrap, widget benchmark/browser/resource/profile scripts (including the
profile bootstrap), and `integration_tests/integration/processes.py` plus parent
package initializers. The process helper starts the benchmark's subprocesses;
it must trigger the widget lane even though it lives under integration tests.

Changes confined to books, catalog, documents or adoption-report checking still
run their integration lane, but do not run the unrelated four-job widget matrix.
When adding a cross-app import, subprocess, file read or bootstrap dependency,
update the widget inputs in the same PR. These are explicit audited dependencies,
not a claim that static imports discover arbitrary runtime behavior.

The planner JSON explains every lane decision. An affected test outside all lane
roots runs every configured lane and names the missing mapping; add that directory
to a lane whose pytest command actually executes it.
# CI routing checks

The always-running `ci-contracts` job checks this repository's actual lane
configuration against real Git snapshots. README/prose and repository metadata
must not launch library, application, or widget benchmarks. Workflow inputs are
owned by their execution lanes: `ci.yml` affects library/integration/type checks;
`widget-store.yml` affects the widget comparison. Dependency and planner
configuration changes still run every lane, as do full checkpoints and missing
history. Python changes still run type checking.

Preserve explicit subprocess inputs: the widget benchmark uses shared settings,
Celery bootstrap, profiling scripts, and the integration process helper. A new
dynamic dependency must be declared with its consuming lane. Docs links and both
distribution descriptions are validated even when all runtime lanes skip.
