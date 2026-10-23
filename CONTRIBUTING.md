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

Use the Conventional Commits policy below for PR titles. Explain the resulting
behavior, relevant validation and limitations.

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

`.github/test-lanes.json` uses pytest-selective to select complete library,
integration, widget comparison, type-check, CI contract and wheel runtime lanes.
Build/metadata and lint checks remain available without starting tests.
Main, manual dispatch and `test:full` run complete suites.
A failed planner executes the affected workflow in full, but the regular test
gates require successful planning before merge.

Workflow inputs name execution jobs, such as `.github/workflows/ci.yml#tests`.
An edit to that job runs its library and integration lanes; a type-check job edit
runs type checking. Workflow-level settings still affect every lane in that
workflow. Selector pin changes, reporting, comments and unrelated jobs do not
start test services. Every execution-workflow edit selects the CI contract tests, which
check the pinned selector and classify every job. If tests consume another job's output,
declare that job as an additional input: job dependencies are not inferred.

For automatic PR events, CI-esta can reuse each Django variant independently
after it has completed the same whole-tree inputs within 24 hours. The receipt
binds the tested merge tree, base, head tree and shared tooling; it is recorded
only after all admitted tests, obligation validators, setup checks and artifact
uploads pass. A missing, stale or contradicted proof starts that variant with
fresh PostgreSQL and Redis. Both `tests (locked)` and `tests (minimum)` remain
required, even when prior proof allows a test runner to be skipped.

Execution evidence also distinguishes library-only, integration-only and combined
validation through CI-esta's `execution-context`. A narrower run cannot prove a
later broader run, even when the Git inputs are identical. Job names stay stable,
so a later failure revokes older proof across scopes. Planner fallbacks and missing or inconsistent
plan metadata execute fresh work without issuing reusable evidence. This keeps
broader fallback results from hiding a later failure in the normal, narrower scope.

Explicit reruns execute the requested lanes again. Main, manual dispatch and
`test:full` bypass reuse entirely. The time limit bounds reuse across hosted
runner and service-image updates outside Git; full checkpoints still exercise
the current environment. Reused runs do not renew receipts or upload replacement
test artifacts. Use the original successful run's artifacts, whose receipt
matches the current planner's receipt.

The widget comparison uses the standalone widget app. Its inputs include all
Due runtime/shims, widget code/assets/migrations, shared Django settings and Celery
bootstrap, widget benchmark/browser/resource/profile scripts (including the
profile bootstrap), and `integration_tests/integration/processes.py` plus parent
package initializers. The process helper starts the benchmark's subprocesses;
it must trigger the widget lane even though it lives under integration tests.

Changes confined to books, catalog, documents or adoption-report checking still
run their integration lane, but do not run the unrelated ten-capture widget matrix.
When adding a cross-app import, subprocess, file read or bootstrap dependency,
update the widget inputs in the same PR. These are explicit audited dependencies,
not a claim that static imports discover arbitrary runtime behavior.

The planner JSON explains every lane decision. An affected test outside all lane
roots runs every configured lane and names the missing mapping; add that directory
to a lane whose pytest command actually executes it.
# CI routing checks

The selected `contracts` lane checks this repository's actual lane
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

## Measured integration evidence reuse

On 2026-10-22, PR #78 compared a fresh run with an ordinary label event on the
same head (`59db75dc36dcbb294ede24e975590e863a03987c`) and tested merge
(`24e466876255e80b4725ccb07865fd6bd28f5efa`). Both used the same lane decisions:
library, integration and type checking were selected. Only completed CI-esta
evidence removed the repeated Django execution.

| CI case | Run / attempt | Required gates elapsed | Summed runner seconds | Started jobs | Django service jobs |
| --- | --- | ---: | ---: | ---: | ---: |
| No matching proof | [39860945819 / 1](https://github.com/gigaverse-app/due/actions/runs/39860945819/attempts/1) | 594 s | 908 s | 9 | 2 |
| Identical inputs with proof | [39862232992 / 1](https://github.com/gigaverse-app/due/actions/runs/39862232992/attempts/1) | 42 s | 100 s | 7 | 0 |

This pair saved 808 runner seconds (89.0%). The avoided locked and minimum
Django jobs used 459 and 349 seconds respectively. Both required test gates,
lint, type checking, packaging and CI contract checks passed in both runs.
The reuse run emitted no replacement execution receipt or test artifact.

Elapsed time is workflow creation to completion of all six named required
checks, so it includes admission delays. The first runner started after 88 s
in the fresh run and 4 s in the reuse run; elapsed savings are not entirely
test execution savings. Runner seconds sum actual job durations and are not
rounded billing minutes or a prediction for every future run.

CI-esta reports for both source attempts completed without collection errors.
Their separate observers used another
[21 runner seconds](https://github.com/gigaverse-app/due/actions/runs/39862194815)
and [18 runner seconds](https://github.com/gigaverse-app/due/actions/runs/39862334245)
respectively, outside the validation dependency chain and the table above.
The superseded predecessor run `39860104313 / 1` used 762 observed runner
seconds before cancellation; it is iteration cost, excluded from this successful
same-revision comparison.

The [explicit rerun control, attempt 2](https://github.com/gigaverse-app/due/actions/runs/39862232992/attempts/2)
resolved no reusable variants and started both Django service jobs on the same
tested merge. It was deliberately cancelled after verifying execution admission,
before pushing the main merge and this measurement record. Its verification
cost is separate from the successful attempt-1 comparison above; it is not a
second completed full-suite result.
# Measured parallel conformance

The standalone package-export check now runs beside the locked/minimum Django
lanes with its own PostgreSQL and Redis services. Both required test gates depend
on it whenever the library lane is selected, even if a Django receipt is reused.
Its workflow input is `.github/workflows/ci.yml#package-export`; keep that input
and the real-Git routing regression when editing the job.

On 2026-10-23, the fresh serial-export control
[39927047157 / 1](https://github.com/gigaverse-app/due/actions/runs/39927047157/attempts/1)
and parallel-export run
[39927107915 / 1](https://github.com/gigaverse-app/due/actions/runs/39927107915/attempts/1)
measured 386 to 320 seconds to the validation gate, and 289 to 235 seconds on
the execution critical path. Both used 548 total runner seconds. This trades
parallel service startup for shorter dependency depth; it does not claim a
runner-cost reduction. Both Django variants, package independence, every
conformance validator and all existing required check names remain intact.
An earlier pair improved execution time but lost elapsed time to admission
delays, so one run is not a general turnaround guarantee.

The full validation gate improved from 538 to 282 seconds in the controlled
serial/four-worker pair, with identical 982 integration test identities and
outcomes per Django variant. Every conformance validator remained enabled.
The comparison includes package-export independence and library controls;
Widget Store is a separate workflow.

See the [raw critical-path report and test inventories](https://github.com/gigaverse-app/ci-esta/blob/main/docs/benchmarks/2026-10-23-critical-execution.md).
Use `-n 4 --dist worksteal` for the package and integration sessions, and keep
profile/effect/observation validation after pytest. Database isolation, unique
worker queues and controller-side report merging are required, not optional.

## Conventional Commits

Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)
for commits you create and PR titles (the squash commit that lands on `main`):

```text
fix(parser): handle empty input
feat: add a new output format
feat(api)!: remove the deprecated argument
docs: clarify installation
```

Allowed types are `feat`, `fix`, `perf`, `revert`, `docs`, `style`,
`refactor`, `test`, `build`, `ci`, and `chore`. Scope is optional.
Use `!` before the colon for breaking changes and explain the migration in the
PR body. Choose the type for the actual change, since release tooling uses it.

The `conventional-commit` check uses
[amannn/action-semantic-pull-request](https://github.com/amannn/action-semantic-pull-request).
It reads PR metadata, including for forks, without checking out contributor code.
If it fails on a title, rename the PR; the edited event reruns the check.
Keep the existing PR and commits. Work-in-progress commits need not be rewritten.

Use **Squash and merge**, with the PR title as the commit title. Check the final
message before merging, including single-commit PRs, and preserve any
`BREAKING CHANGE:` explanation in the body. The repository's squash defaults
support this, but a maintainer can still edit the final message.
See [RELEASING.md](RELEASING.md) for versioning and publishing.

## Shared instructions for Codex and Claude

[AGENTS.md](AGENTS.md) is the canonical repository guidance. Codex reads it
directly; Claude reads the tracked `CLAUDE.md -> AGENTS.md` symlink. Edit
`AGENTS.md` once, including commit-policy changes, so both tools receive the
same instructions.

[dallay/agentsync](https://github.com/dallay/agentsync) manages that link through
[agentsync.toml](agentsync.toml). After cloning, or to repair a
missing or stale link, run these commands from the repository root with Node.js
18+ and npm installed:

```bash
npx --yes @dallay/agentsync@1.52.3 apply
npx --yes @dallay/agentsync@1.52.3 status
```

The CLI version is pinned for reproducible setup. Review and update both commands
when upgrading AgentSync. The configuration disables managed gitignore changes
because we intentionally commit the relative link, following AgentSync's
[committed-destination workflow](https://dallay.github.io/agentsync/guides/gitignore-team-workflows/).
Ordinary instruction edits propagate immediately through the link.

On native Windows, enable Developer Mode and Git symlink support
(`git config core.symlinks true`), then run `apply` to repair a checkout that
materialized the link as plain text. Alternatively, use WSL for a checkout in
the Linux filesystem. See the
[Windows setup guide](https://dallay.github.io/agentsync/guides/windows-symlink-setup/).
If AgentSync backs up an existing `CLAUDE.md`, review and preserve any unique
guidance in `AGENTS.md`; do not commit the backup as another instruction source.

## Documentation-only CI

Documentation-only PRs execute zero tests unless a test or lane explicitly
declares that documentation as an input. This includes CI contract tests,
installed-wheel smoke and public-action smoke. Metadata/link/build checks may
still validate documentation without running runtime tests. Declare execution
workflows, selector configuration, helpers, locks and non-import inputs; missing
history or failed planning must execute the full affected lanes and fail the
required planning gate. Main/manual/full checkpoints remain complete runs.
