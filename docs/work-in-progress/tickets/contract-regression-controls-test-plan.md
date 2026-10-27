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
- The document control and unchanged document conformance suite pass together: 68 tests. The profile report is generated with `--due-work-profile-report`.
- Ruff, formatting and Pyrefly pass. The full package suite passes: 1,479 tests with real PostgreSQL and an isolated local Redis instance. The first attempt lacked the real-worker tests' Redis endpoint; supplying the required service resolved all six failures.
- The dependency source is pytest-obligation's merged PR #186, revision `08d68eedabf0aea393ffd8917a7a872561f376ea`.
