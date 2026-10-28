# Static eligibility and captured membership reuse

One upstream PR, two separately measured changes. Preserve provider safety,
durable receipts, retry subsets, and database lease authority.

- [ ] Static Q: allowed/blocked direct and queued claims; explicit clocks;
      fallback deadlines without mutating declarations or bypassing backoff.
- [ ] Live bulk crossing: inline and separate receipts; exact locked members;
      no redundant membership SELECT; no cross-invocation state.
- [ ] Failure boundaries: attempt rollback, duplicate/missing evidence,
      ambiguous acceptance, receipt recovery, partial retries, expired fences.
- [ ] Existing generated application contracts and enrollment reports.
- [ ] Benchmark baseline, static only, membership only, combined using the same
      mail workload and PostgreSQL; report commands, writes, commits and timing.
- [ ] Required CI, lint/typecheck, docs, and Conventional Commits PR title.
