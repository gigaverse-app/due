# Checked immutable-input declarations

Source: gigaverse-backend c64a0249f (#5347).

- [x] Port the healthy and faulty production capability controls before the implementation: claimed-input movement, no-op perturbation, paths that do not revisit work, dependent wakes, and parked-row behavior.
- [x] Add perturb_inputs and convergence_not_assessed to the canonical public EffectExamples type; generated proofs use real admission, claim and recovery.
- [x] Preserve selection, report origin, evidence and mandatory-collection behavior after the new generated proof is enrolled.
- [x] Adapt Due's real document/books declarations where the stronger contract requires evidence.
- [ ] Run complete PostgreSQL/Redis suites and required primitive/application reports against the companion public harness.
- [ ] Rehearse the 17 current BE registrations and application journeys against the installed public owners.

Update the public field reference and examples alongside the implementation. BE table migration and deployment are separate from these library proofs.

Local full run after merging main #14: **1,250 passed, 4 xfailed**. Two expected failures are the new, baselined book input-immutability binding debt; two are deliberate test controls. Ruff and Pyrefly pass. The final report-producing run against the companion package remains below.
