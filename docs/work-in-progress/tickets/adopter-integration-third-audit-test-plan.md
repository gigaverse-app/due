# Real adopter integration and third BE audit

Move runnable application bindings onto ProviderSeam and prove failure-preserving scopes with real provider/product outcomes; preserve existing recovery and process-death guarantees.

- [x] Inventory every capability changed by #57/#22 against executed adopters; distinguish reusable mechanism proofs from application claims.
- [x] Migrate duplicated example bindings to canonical public owners, keeping actual application entry points and independent provider observations.
- [x] Add positive/negative controls at real adopter boundaries; demonstrate broken behavior is caught without fabricating production state transitions.
- [x] Run relevant examples/demos/apps and enforce their executed coverage in CI; document exact scope and existing gaps.
- [x] Audit current pre-cutover BE source, nested helpers, tests and tooling for omitted reusable behavior; port any findings with regression evidence.
- [x] Run standalone and paired BE validation; verify exact public/subtree trees and reviewable follow-up PRs.

Evidence: all five applications / six capabilities now use the canonical seam;
real book mutation and catalog mutation controls preserve failing verdicts.
The paired standalone run passed 1,468 tests with four existing strict findings.
Ruff, formatting and Pyrefly pass. The associated public and BE PRs record the
exact candidate revisions, whole-tree parity and final CI results.
