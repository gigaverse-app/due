# Static eligibility and captured membership reuse

One upstream PR, two separately measured changes. Preserve provider safety,
durable receipts, retry subsets, and database lease authority.

- [x] Static Q: allowed/blocked direct and queued claims; explicit clocks;
      fallback deadlines without mutating declarations or bypassing backoff.
- [x] Live bulk crossing: inline and separate receipts; exact locked members;
      no redundant membership SELECT; no cross-invocation state.
- [x] Failure boundaries: attempt rollback, duplicate/missing evidence,
      ambiguous acceptance, receipt recovery, partial retries, expired fences.
- [x] Existing generated application contracts and enrollment reports.
- [x] Benchmark baseline, static only, membership only, combined using the same
      mail workload and PostgreSQL; report commands, writes, commits and timing.
- [x] Lint/typecheck, docs, and Conventional Commits PR title.

Final required Linux CI and standalone-export results are tracked on Due #158.
The first export run exposed a pytest-version-dependent test setup; the fixture
now explicitly constructs the instance override it promises to test.
