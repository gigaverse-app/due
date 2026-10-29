# Bounded settlement prototype

Use real PostgreSQL and the existing independent fake-provider ledger. Production
claims, crossing receipts, model saves, completion hooks and registered recovery
remain in the execution path. Run-specific logs and benchmark traces belong in the
separate evidence branch, not this shipping change.

- [x] Shared commits and bounded flush: `attempt_many` with three rows and size two;
  observe committed receipts, delayed product progress, provider autocommit and
  independent exact acceptances.
- [x] Existing `attempt_execution` retains committed completion before return.
- [x] Poisoned completion: rollback the group, settle healthy rows individually,
  recover the failed row using its receipt, with no provider replay.
- [x] Generated pytest-obligation convergence: crash before flush, run registered
  recovery, prove exact product results and one provider acceptance per member.
- [x] Shared sensitivity controls detect disabled recovery and duplicate effects.
- [x] Process death during group settlement rolls back all product writes while
  preserving separately committed receipts; registered recovery finishes them.
- [x] A competing fencing token prevents an old buffered completion from writing.
- [x] Provider failure flushes prior receipts and stops further provider calls.
- [x] Invalid bounds reject before any external effect.
- [x] Soft worker limits stop without entering per-row fallback (red before fix).
- [x] Generated commit-death histories across two settlement groups prove that
  the registered recovery finishes the remaining receipts after each interruption.
- [x] Token, revision and state replacement each refuse stale publication; saves
  and post-save signals still run with their persisted completion timestamp.
- [x] Existing bulk/runtime regression suites, lint, typing, CI and exported package.
- [x] Source-pinned native, ordinary Due and prototype mail benchmarks: CPU, wall
  latency, queries, DML, transactions, database planning/execution, buffers and WAL.

Limitations to expose: shared visibility, longer lock span, replay-safe local
hooks, count rather than wall-time bound, and completion-failure classification
remaining CLAIMED until ordinary recovery. No new claims of provider exactly-once
delivery or safe replay after an unacknowledged provider call.
