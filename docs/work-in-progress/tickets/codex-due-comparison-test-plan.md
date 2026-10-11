# Due comparison and application evidence

Build on the merged books demo without weakening its existing recovery proofs.
Compare the same external SQLite index and product outcome with a Procrastinate
implementation using transactional admission, idempotent effects and stalled-job
recovery. This is a correctness/application-complexity comparison, not a throughput
benchmark or a claim that a tutorial represents Procrastinate's limits.

- [ ] Shared books histories — normal completion, admission rollback, duplicate
  execution and real worker death after external commit; both implementations
  must expose one searchable book using production admission and recovery.
- [ ] Mutable catalog — an old captured write finishes after a newer revision;
  use Due Projection and verify product fencing and eventual external repair.
- [ ] Uncertain operation — a real local provider commits and loses its response;
  observe correlated evidence, never resend on an inconclusive observation.
- [ ] Setup diagnostics — share the registration setup; reject missing task
  imports, mismatched routes and missing recovery schedules; report recovery
  freshness through existing runtime instrumentation without claiming a remote
  worker is healthy from local configuration alone.
- [ ] Run relevant generated contracts and integration tests, mutation controls,
  full CI matrix, type/lint/migration checks; record measured results and limits.

Maintain immutable books as the two-stage handoff example. The mutable catalog
and uncertain operation have separate lifecycle owners and runnable commands so
their semantics are visible without adding hidden modes to that first example.
