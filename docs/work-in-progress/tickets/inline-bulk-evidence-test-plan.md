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
- [x] Shared generated obligation histories: run both receipt storage choices
  against the same independent provider/product/evidence observers and verify reports.
- [x] Recovery plan: populated history, sparse pending receipt, missing-index control.
- [x] Compatibility: old scalar/custom receipt stores, old migrations and types.
- [x] Performance: clean committed sources; reversed serial blocks, warmup plus
  repeated success/retry/recovery cases, identical provider behavior. Measure
  SQL, writes and commits individually; elapsed time is local evidence only.

No real provider traffic. PostgreSQL is real; only the provider boundary is scripted.

Executed on `40e2075`: 1,800 local tests; profiles A/B/C/E/J for both storage
choices; 1,806 package tests and 1,072 integration tests (two declared xfails)
plus 286 application tests in Linux CI. Shared regression controls also prove
the independent observers detect broken uncertainty settlement and duplicate
provider entry.

[Paired measurements and raw samples](https://github.com/gigaverse-app/due/pull/150#issuecomment-6439712042)
show five fewer SQL statements and three fewer DML statements per successful
crossing, with unchanged commit count. Fifty-member/large-result tests show
essentially unchanged WAL volume. This proves fewer round trips, not lower
physical write volume or full mail parity.
