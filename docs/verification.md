# Verification

The standalone suite combines extracted work-table regressions, generated
`pytest-obligation` contracts, and a real-service example.

- PostgreSQL regressions exercise atomic producer rollback, fencing, concurrent
  delivery, lost reservation recovery, retry exhaustion, revision changes,
  eligibility, continuations, compensation, poisoned recovery and receipts.
- The generated contract binds the production selector, coordinator and recovery
  path for profiles A (recovery), B (ownership), D (retention), I (admission), and
  J (retry limits). Other profiles do not apply to that simple immutable example;
  they are not claimed as library-wide certifications. Specialized evidence and
  projection tests exercise those modes separately.
- The worker test starts a separate Celery prefork process, sends recovery through
  Redis, reads/writes PostgreSQL and checks the actual filesystem publication.
  It intentionally drops the initial post-commit notification.
- The public fault controls only drop delivery, advance clocks or interrupt a
  real operation. They do not fabricate successful settlement.

`due.django.conformance.DurableWorkBindings` is an optional testing adapter,
installed with `django-due[testing]`. It delegates to the same coordinator as the worker.
Applications still need their own callback and provider-contract tests; the library
cannot infer remote idempotency or prove a provider's promise.

Run `uv run pytest --due-work-profile-report=profiles.json` against dedicated
PostgreSQL/Redis services. CI retains the generated profile report. Ruff, Pyrefly,
migration consistency and wheel/sdist builds are separate gates. Claims about
fault recovery do not imply a throughput benchmark, a deployed backend cutover,
or that every provider integration has been validated.

## Initial extraction evidence

On Python 3.14.7, PostgreSQL 17 and Django 6.0.9, the suite passed **382 tests**,
including **44 generated contract cases** and the separate Redis/Celery worker
journey. Ruff, Pyrefly, migration checks and wheel/sdist builds passed.
The installed wheel also passed imports, Django checks and actual migrations in a
clean environment with Django 6.1.2 and no checkout on its import path.

The generated retention positive control was mutation-checked: temporarily
returning zero from production `prune_completed_work` made
`D-assert_retention_prunes_settled_work` fail; restoring the implementation made
the same case pass. The broken implementation was not committed.

[Initial CI run](https://github.com/gigaverse-app/due/actions/runs/38098113273)
ran the full check workflow successfully for the extraction implementation.

## Contributor workflow and parity follow-up

The expanded PostgreSQL/Redis suite passes **461 tests**, including the same
44 generated contract cases. Added proofs cover registry/Beat routing,
dependent wakes and lock ordering, mirror rollback and receipt repair, closed
execution gates with unresolved evidence, operation recovery isolation and soft
limits, read-only operator inspection, and private delivery arbitration.

CI runs the suite on both locked Django and the declared minimum (6.0.8), with
independent lint/workflow, Pyrefly and installed-wheel jobs. The wheel smoke test
runs with isolated Python and no development dependencies, checks pure-contract
imports, loads every runtime module, and verifies the typing marker, migration
and management command are included.

The report guard requires profiles A, B, D, I and J to have actually executed
and passed. Its negative controls reject an empty report and a report whose
stored `verified`/`passed` flags claim success while a required case was not
selected: verdicts are recomputed from canonical pytest-obligation models.

[Follow-up CI](https://github.com/gigaverse-app/due/actions/runs/38099055025)
passed all five jobs for implementation commit `2766c33`, including both Django
versions.


## Full work-table harness and replacement audit

The public `effect_contract_suite` now ports the mature backend generator. Its
application mode and policies require the appropriate examples instead of asking
the author to select a convenient subset of profiles. Collection tests cover
missing methods, ignored suites, named variants, skips/xfails, selected subsets,
xdist merging and observation-report provenance. Enrollment tests independently
require examples for every supported work-table mode.

The document adopter claims A/B/D/E/H/I/J, including actual repeated file writes,
revision fencing, partial-admission rollback and visible retry exhaustion. Due's
extra histories check production schedule/worker options, poisoned identities,
lock order, competing workers and the migrated indexes. Real child processes exit
abruptly after admission, before the file write and after the file write. The
shared pytest-obligation process oracle requires recovery to converge, and a
negative control disables production recovery and must fail that oracle.

Operation and observer tests cover UNKNOWN/no-resend, positive and negative
evidence, interrupted local settlement, competing receipts, inconclusive reads,
lost observation delivery and poisoned recovery. Root mutation controls reject
inert observers, hidden provider calls, missing dispatch, stale tokens, early
notifications, partial admission leaks and invisible exhaustion. These are local
histories with controlled seams; they do not certify every remote provider or
replace the backend's adopter suites.

CI retains profile, application-suite and observation reports and checks complete
executed application evidence. See [conformance authoring](conformance.md) and the
[backend replacement audit](backend-adoption.md) for the API and remaining cutover
work. The native-table API remains excluded.

Local parity run: **821 tests pass** on Python 3.14.7, Django 6.0.9 and
PostgreSQL 17, including the Redis/prefork worker and real process-death histories.
The document suite has **64 generated cases**. Ruff, Pyrefly, migration drift,
profile/application evidence gates, wheel/sdist build and an isolated runtime-only
wheel install with Django 6.1.2 pass.
