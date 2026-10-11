# Current backend parity

Port backend #5350 provider helpers, #5351 selection-completeness proofs and #5332 unclean recovery observations. Keep generic Django proofs in pytest-obligation and runtime-bound discovery in Due.

- [x] Port and run real PostgreSQL controls for omitted own/related state branches, lying exclusions, broken ticks, deferred work and strict per-combination debt.
- [x] Preserve SQL-failure isolation, whitespace edges and lifecycle path behavior.
- [x] Exercise Due discovery through generated suites; run recovery metric controls for clean, failed, aborted and non-heartbeat ticks.
- [ ] Run framework-free imports, lint, types and package suites.
- [ ] Refresh BE baseline/public rehearsal at the pinned current main, compare identities/outcomes and document every change.

Production cutover remains separate from this harness and behavior parity work.

Controls include a complete/missing discovery branch through real derivation, refusal to drop the generated discovery case at collection, and ProviderSeam compilation through the public contract API. Full suite before the final collection controls: 1,298 passed and four existing xfails. Final locked-dependency run and report gates pending.
