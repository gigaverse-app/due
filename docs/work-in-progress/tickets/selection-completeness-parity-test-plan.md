# Current backend parity

Port backend #5350 provider helpers, #5351 selection-completeness proofs and #5332 unclean recovery observations. Keep generic Django proofs in pytest-obligation and runtime-bound discovery in Due.

- [x] Port and run real PostgreSQL controls for omitted own/related state branches, lying exclusions, broken ticks, deferred work and strict per-combination debt.
- [x] Preserve SQL-failure isolation, whitespace edges and lifecycle path behavior.
- [x] Exercise Due discovery through generated suites; run recovery metric controls for clean, failed, aborted and non-heartbeat ticks.
- [x] Run framework-free imports, lint, types and package suites.
- [x] Refresh BE baseline/public rehearsal at the pinned current main, compare identities/outcomes and document every change.

Production cutover remains separate from this harness and behavior parity work.

Controls include a complete/missing discovery branch through real derivation,
refusal to drop the generated discovery case at collection, and ProviderSeam
compilation through the public contract API. At functional revision `87e7b12`,
locked and minimum-Django CI each passed 1,301 tests with four existing xfails;
all six report gates, startup configuration and migration checks passed.

BE `fd921da14` preserved all 2,075 baseline identities/outcomes through the public
packages and gained 15 passing admission guards: 2,020 passed, 70 existing
xfails. The checked-in #5355 runner reproduced the result from a fresh archive.
Ruff and Pyrefly pass. The failure-first SQL, seam-enrollment and collection-loss
controls are retained alongside the imported BE controls.
