# PostgreSQL runtime contracts

Run the retained claim, settlement, retry, and enrollment regressions with:

```sh
uv sync --all-packages --locked
uv run python scripts/check_runtime_contracts.py
```

Configure `DUE_DATABASE_URL` for a dedicated PostgreSQL database whose user can
create test databases. The default is the repository's development database.
The tests create UTF-8 databases from template0.

## What is checked

The ownership tests cover integer and UUID identities, committed and fused
claims, stale identity/state/token/revision, wrong models, and malformed current
fields. Retry tests compare capped exponential delays with independent exact
integer arithmetic, including large steps and invalid configuration.

The PostgreSQL matrix observes callback entry and order, committed writes,
receipts, rollback, and fallback for two- and three-row batches. Missing and
duplicate completions must not enter callbacks. Persistent callback faults must
remain visible after rollback and ordinary fallback.

`contracts/runtime-checks.json` independently enrolls 68 native cases and 155
generated application-contract cases, including requested smoke exploration.
The runner rejects missing, duplicate, skipped, or errored native cases and
incomplete generated effect reports. Collection separately requires requested
exploration cases, so deleting a generated method cannot silently shrink the
required suite.

## Sensitivity and evidence

Three controls modify disposable copies of the actual runtime source: bypass
claim admission, ignore the fencing token, and suppress a persistent callback
failure. Each must fail its designated PostgreSQL history with the expected
semantic assertion. Import attestation confirms the test ran the modified copy;
setup errors or missing tests do not count as detection. The runner executes the
unmodified 223-case baseline again after controls.

The `runtime-contracts` CI job selects the `runtime_contracts` lane through
reader-owned dependencies. The aggregate gate requires it whenever selected.
Artifacts under `test-results/runtime-contracts/` contain JUnit results,
application effect/profile reports, imported source hashes, command logs,
control outcomes, and the final summary.

These tests cover the enrolled histories and admitted field types. They do not
prove every possible callback, database execution, or concurrent schedule.
No verifier, proof compiler, generated theorem artifacts, or model interpreter
is required.
