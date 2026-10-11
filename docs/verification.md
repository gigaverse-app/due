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
installed with `due[testing]`. It delegates to the same coordinator as the worker.
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
