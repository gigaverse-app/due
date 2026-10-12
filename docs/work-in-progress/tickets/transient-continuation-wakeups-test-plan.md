# Transient continuation wakeups

The persisted work row and normal eligibility/claim/fencing path remain authoritative.
The broker hint is disposable, bounded, and creates no SQL ledger.

- [x] Storefront completes all ten timed stages through real tasks without Beat.
- [x] Hint publication and early/stale/duplicate delivery add no SQL writes or extra stages.
- [x] Lost hints and broker failure preserve one work row, retry budget, and full recovery.
- [x] Generated pytest-obligation process histories kill the worker after the continuation commits but before its hint and recover with every hint suppressed; negative controls reject missing recovery and corrupt progress/refunds.
- [x] Shared scheduled-action cancellation, replacement, timing and recovery tests still pass.
- [x] Wire the new proofs into locked/minimum CI and package selftests; keep paired real DBOS/Due benchmarks and browser crash journeys required. Final run results belong in the PR.

Use the existing BackgroundWork broker/clock driver and real PostgreSQL commands.
Benchmark both arm orders on CI with unchanged upstream source and identical waits;
report latency and SQL row operations separately, without a timing gate.
