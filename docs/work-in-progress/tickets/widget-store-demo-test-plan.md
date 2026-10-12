# Widget Store: application outcomes and matched comparison

Build an original Due implementation of the DBOS Widget Store scenario, preserving checkout identity, scarce inventory, payment callbacks, failed-payment release and ten resumable fulfillment stages. Keep the pinned original runnable in an isolated comparison environment. The UI includes a supervised crash/restart control. Payment remains simulated; no real money or provider is involved.

- [x] Checkout journeys — real commands/HTTP, PostgreSQL inventory and orders; success, refusal, duplicate keys, contradictory payment callbacks, timeout and late payment policy.
- [x] Concurrent last-item reservations — separate database connections, one winner and stock conservation.
- [x] Generated contracts — admission, rollback, lost dispatch, gating, continuation, process death and independent expected outcomes through actual registered recovery.
- [x] Negative controls — duplicate release, bypassed payment gate, dropped continuation, skipped progress, omitted proof evidence; production mutations must fail the generated/application oracle.
- [x] Live UI — browser checkout, payment success/failure, stock display and crash/restart; real HTTP, Celery and Beat, with bounded supervisors that clean up owned processes.
- [x] Matched DBOS/Due comparison — pinned upstream source/dependencies; equal initial stock, request count, ten one-second stages, no hidden client retries; success/failure and actual crash recovery, independent database assertions and measured SQL writes/latency.
- [x] CI — complete required application proofs, negative controls, browser crash journey and both benchmark arms; publish raw measurements as artifacts, never performance thresholds from noisy runners.

Runtime design must reuse Due's ownership, eligibility, continuations and recovery. Product inventory transitions remain atomic and idempotent. No new workflow engine or application reaper. Any deviation from the original scenario is identified in the comparison documentation. Measurements are bounded workflow experiments, not capacity or throughput claims.
