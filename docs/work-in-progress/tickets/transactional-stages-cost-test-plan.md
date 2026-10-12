# Transactional application steps and measured cost

Keep existing work rows and recovery authority. Separate PostgreSQL-only transitions
from external effects, so the former can commit product progress and continuation
in one fenced transaction without an application replay ledger.

- [x] Measure both applications' SQL calls/time, buffer activity, WAL, commits, process/container CPU and broker traffic; publish dimensions and attribution limits, not a synthetic total-cost score.
- [x] Execute an eligible timer directly in its worker with registered execution limits, retaining exact deadline/revision validation and ordinary atomic claim/fencing.
- [x] Introduce a small transactional callback on the existing engine; prove rollback, stale ownership, duplicate delivery, continuation, terminal completion and downstream handoff.
- [x] Migrate Widget Store onto the callback and remove superseded stage/replay bookkeeping while preserving reservation, payment, refund and dispatch outcomes.
- [x] Strengthen generated continuation bindings with explicit terminal outcomes, timed progress and fault controls; require report entries in CI.
- [x] Exercise payment/expiry and repeated-failure boundaries through production paths.
- [x] Assess other demos and the backend's existing binding for reuse; preserve external-effect boundaries rather than treating provider calls as database transactions.
- [x] Run full locked/minimum CI and paired original DBOS/Due benchmarks with unchanged business waits; keep evidence in artifacts and PR discussion.
