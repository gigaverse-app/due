# Due comparison and application evidence

Build on the merged books demo without weakening its existing recovery proofs.
Compare the same external SQLite index and product outcome with a Procrastinate
implementation using transactional admission, idempotent effects and stalled-job
recovery. This is a correctness/application-complexity comparison, not a throughput
benchmark or a claim that a tutorial represents Procrastinate's limits.

- [x] Shared books histories — normal completion, admission rollback, duplicate
  execution and real worker death after external commit; both implementations
  must expose one searchable book using production admission and recovery.
- [x] Mutable catalog — an old captured write finishes after a newer revision;
  use Due Projection and verify product fencing and eventual external repair.
- [x] Uncertain operation — a real local provider commits and loses its response;
  observe correlated evidence, never resend on an inconclusive observation.
- [x] Setup diagnostics — share the registration setup; reject missing task
  imports, mismatched routes and missing recovery schedules; report recovery
  freshness through existing runtime instrumentation without claiming a remote
  worker is healthy from local configuration alone.
- [x] Run relevant generated contracts and integration tests, mutation controls,
  full CI matrix, type/lint/migration checks; record measured results and limits.

Maintain immutable books as the two-stage handoff example. The mutable catalog
and uncertain operation have separate lifecycle owners and runnable commands so
their semantics are visible without adding hidden modes to that first example.

## Evidence

After merging current main (including PRs #7 and #9), the full local suite passed:
1,210 tests on Python 3.14, PostgreSQL 17, Redis and locked Django. All six
conformance report gates passed. The shared comparison contributes ten cases:
five failure/outcome histories on each implementation. Both new command paths
also pass through real Celery workers.

The projection suite includes a negative control that suppresses repair. A
separate temporary mutation made the printer observer inert; its generated
inconclusive-observation proof failed on the missing provider read. The mutation
was not committed. Runtime validation, Ruff, Pyrefly, workflow validation and
migration drift checks passed. The only local warning came from the pre-existing
prefork observation smoke test imported from main (Python's multithreaded fork
warning).

The printer deliberately declines harmless replay and retains evidence
indefinitely; CI verifies A/B/C/I/J plus its complete generated effect report.
The catalog verifies A/B/D/E/H/I/J. The existing books and document gates remain.
The comparison is not a throughput, latency or database-cost benchmark.
