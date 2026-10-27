# Inline bulk evidence

Invariant: exact membership commits before provider entry; retained provider
evidence never grants a duplicate call, disappears during rollback/retention,
or gets overwritten by a concurrent partial receipt or diagnostics writer.

- [x] Public bulk execution: success and subset retry use one attempt per call,
  no companion receipt rows, and preserve independently observed provider/product outcomes.
- [x] Late/contradictory evidence: public receipt acceptance and derive preserve
  no-resend behavior and reject contradictions against committed evidence.
- [x] Real PostgreSQL concurrent writers: stale input objects, row-lock waits,
  partial receipt merges and metadata in either order retain both updates.
- [x] Database acceptance failure: a real CHECK violation rolls back receipt
  and verdict, blocks replay, and permits later successful receipt recovery.
- [x] Migration/retention negative controls: tests first failed against lossy
  rollback and generic attempt compaction; guards preserve inline evidence.
- [ ] Shared generated obligation histories: run both receipt storage choices
  against the same independent provider/product/evidence observers and verify reports.
- [ ] Recovery plan: populated history, sparse pending receipt, missing-index control.
- [ ] Compatibility: old scalar/custom receipt stores, old migrations and types.
- [ ] Performance: clean committed sources; reversed serial blocks, warmup plus
  repeated success/retry/recovery cases, identical provider behavior. Measure
  SQL, writes and commits individually; elapsed time is local evidence only.

No real provider traffic. PostgreSQL is real; only the provider boundary is scripted.
