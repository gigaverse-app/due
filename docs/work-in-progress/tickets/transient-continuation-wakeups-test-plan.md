# Transient continuation wakeups

The persisted work row and normal eligibility/claim/fencing path remain authoritative.
The broker hint is disposable, bounded, and creates no SQL ledger.

- [ ] Storefront completes all ten timed stages through real tasks without Beat.
- [ ] Hint publication and early/stale/duplicate delivery add no SQL writes or extra stages.
- [ ] Lost hints and broker failure preserve one work row, retry budget, and full recovery.
- [ ] Generated pytest-obligation process histories recover with every hint suppressed; negative controls reject missing recovery and corrupt progress/refunds.
- [ ] Shared scheduled-action cancellation, replacement, timing and recovery tests still pass.
- [ ] Locked/minimum CI, package selftests, paired real DBOS/Due benchmarks and browser crash journeys pass.

Use the existing BackgroundWork broker/clock driver and real PostgreSQL commands.
Benchmark both arm orders on CI with unchanged upstream source and identical waits;
report latency and SQL row operations separately, without a timing gate.
