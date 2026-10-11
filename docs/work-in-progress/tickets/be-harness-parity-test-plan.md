# Checked immutable-input declarations

Source: gigaverse-backend c64a0249f (#5347).

- [x] Port the healthy and faulty production capability controls before the implementation: claimed-input movement, no-op perturbation, paths that do not revisit work, dependent wakes, and parked-row behavior.
- [x] Add perturb_inputs and convergence_not_assessed to the canonical public EffectExamples type; generated proofs use real admission, claim and recovery.
- [x] Preserve selection, report origin, evidence and mandatory-collection behavior after the new generated proof is enrolled.
- [x] Adapt Due's real document/books declarations where the stronger contract requires evidence.
- [x] Run complete PostgreSQL/Redis suites and required primitive/application reports against the companion public harness.
- [x] Rehearse the 17 current BE registrations and application journeys against the installed public owners.

Update the public field reference and examples alongside the implementation. BE table migration and deployment are separate from these library proofs.

Local full run after merging main #14: **1,250 passed, 4 xfailed**. Two expected failures are the new, baselined book input-immutability binding debt; two are deliberate test controls. Ruff and Pyrefly pass. The final report-producing run against the companion package remains below.

The report-producing run also passed (1,250 passed, 4 xfailed); all six CI adoption reports validate. The report gate accepts only the two explicit E-not_assessed book cases, requires their xfail outcomes, and rejects stale or additional debt. Five regression controls pin that exception. All 14 report-gate controls pass.

BE rehearsal exposed a missing handoff environment scope. The public adapter now wraps each history arrangement in the declared environment and retains it through the final observation. A failing regression test pins repeated entry and cleanup; collection still rejects substituted or removed histories. Full rerun: **1,256 passed, 4 xfailed**; collection/enrollment controls: **120 passed**. BE work-table rehearsal: **1,340 passed, 6 existing xfailed**, preserving all 1,331 baseline case outcomes and adding 15 passing admission-binding cases.
