# Contract regression sensitivity

- [x] Add opt-in Due controls that reuse generated proofs and pytest-obligation's baseline, regression and restoration runner.
- [x] Prove product uncertainty settlement is observed by suppressing its finalizer on the selected capability.
- [x] Prove lost-publication recovery is observed by disabling the selected capability's recovery.
- [x] Prove duplicate external entries are observed by repeating the selected operation's real effect.
- [x] Run each control against real PostgreSQL and existing provider/application examples; keep expectations unchanged.
- [x] Check instance scoping, restoration and fresh example lifetimes, including assertion failures.
- [x] Document supported claims, shared API, limitations and downstream adoption; run lint, types and affected conformance checks.

## Evidence

- The initial focused test failed to import the absent controls, before implementation.
- Ten real-SQL package tests pass, covering both receipt representations, fresh scopes, sibling capability isolation, a missing oracle and failed cleanup.
- The control is declared through `EffectExamples.regressions`; the suite owns fixtures, registration, case generation and required collection/report evidence. No application proof-call exception is needed.
- The document suite, enrollment guards and missing-proof collection checks pass together: 217 tests. This includes the existing test-authored production-transition rejection and a new control that deletes the generated sensitivity case and requires collection to fail. The profile report is generated with `--due-work-profile-report`.
- Ruff, formatting and Pyrefly pass. The full package suite passes: 1,479 tests with real PostgreSQL and an isolated local Redis instance. The first attempt lacked the real-worker tests' Redis endpoint; supplying the required service resolved all six failures.
- The initial dependency source was merged obligation #186. The review follow-up pins obligation #187 at `d107f089e62c2bb840f894153d62622ce93b6b45`, raises the published testing floor to 0.23.0, and keeps the release gate resolving only published packages.

## Review follow-up

- `EffectExamples.extras` uses the existing obligation generator. A collection control removes the generated extra and verifies that Due rejects the incomplete suite.
- A real-SQL control proves that a cleanup assertion matching the duplicate-effect diagnostic cannot count as detection. The original production patch is restored.
- The isolated installed-wheel smoke test builds the pinned obligation source as a locally identified candidate, installs Due's testing extra, checks dependency consistency, imports every conformance module and verifies that 0.22.0 is excluded by metadata. It passes. No candidate is published.
- Full Ruff and Pyrefly checks pass. The first full package run identified an AssertionError compatibility issue in the shared scope classifier; obligation #187 corrects it without accepting setup/cleanup failures as sensitivity evidence. The corrected local full suite passed 1,482 tests. CI at `99eb3b409c4e56ff9521b45ee686082e315d9f1d` passed both dependency configurations; the locked run passed 1,487 package tests with fixture validation, 1,072 integration tests with two declared expected failures, and 286 additional tests. The installed testing-extra and standalone export checks also passed.
