# Python source contracts

Due keeps Python as production source. The registered VeriPy project proves
selected function bodies with Dafny 4.11.0, and the required native lane tests
their Django/PostgreSQL callers. Both must pass. The source proof does not
establish a theorem about ORM transactions or arbitrary callbacks.

## Run from a clean checkout

With Python 3.14, uv, Git and PostgreSQL available:

```bash
uv sync --all-packages --locked
.venv/bin/python scripts/bootstrap_source_verifier.py
bash tooling/source-contracts/install-dafny.sh
export PATH="$PWD/.source-contract-tooling/dafny:$PATH"
export DUE_DATABASE_URL=postgresql://due:due@localhost:5432/due_contracts
.venv/bin/python scripts/check_python_contracts.py
```

The PostgreSQL user needs database creation permission for pytest-django. No
Redis, paid API or model service is used by this command. The command rejects
missing/stale proofs, unsupported required source, omitted tests, skipped tests,
collection errors and inconclusive mutation results. It retains backend inputs,
logs, import locations, JUnit, application reports and control results under
`test-results/python-contracts/`; only a complete run writes `summary.json`.

The bootstrap builds a wheel from the pinned public upstream Git commit plus
the reviewed patch in `tooling/source-contracts`. It validates patch, dependency
lock and installed compiler source hashes. It never installs the unrelated
PyPI package named `veripy`. The MIT license and provenance are retained beside
the patch. `--upstream-checkout PATH` supplies the same pinned Git object locally;
`--wheel PATH` additionally requires the exact enrolled wheel hash and installed
source identity. Neither option changes the reviewed compiler tree.

## Source-to-contract mapping

| Production source | Checked property | Boundary evidence |
| --- | --- | --- |
| `package/src/due/retry_delays.py`, `ExponentialDelay.__post_init__` | Invalid delay configurations raise. | Native construction cases. |
| The same module, `ExponentialDelay.at` | For positive initial and maximum at least initial, negative steps raise; successful steps return exactly `min(initial * 2**step, maximum)` in integer microseconds. | Native microsecond, saturation, huge-step and invalid-step cases. |
| `package/src/due/claim_authority.py`, `claim_authorized` | Authorization is exactly model identity plus equality of primary-key kind/value, state, fencing token and desired revision. | `WorkClaim.owns_locked`, `captured_authority`, `row_authority` and the real PostgreSQL callback matrix. |
| The same module, `callback_admissions` | One ordered flag per captured completion; a flag is true exactly for a present row with every ownership fact matching. | `_SettlementBatch._group` locks current rows, removes duplicate identities, builds every flag, and only then enters preparation/publication callbacks. |

`retry_delays.proofs.dfy` contains checked arithmetic lemmas and the exact delay
predicate. Generated definitions are derived from the current production source;
the proof sidecar remains separately maintained. Python annotations and source
comments carry the contracts; no generated Python substitute executes at runtime.

The ownership core is invoked by ordinary `owns_locked` and batch admission.
The SQL selector still uses the original `_authority()` values. Native tests
compare the core with that original predicate, including fused claims whose
stored state/token differ from the claim's eventual state/token. Model mismatch
rejects before reading any fields. Missing/duplicate rows cannot enter a second
callback. The core does not add a lease-expiry condition: unchanged captured
authority retains the existing settlement semantics.

## Types, assumptions and external effects

The normalization adapter accepts exact integer or UUID keys and tags their
domains separately. UUID tokens become their exact 128-bit integer values;
UUID construction from equivalent canonical text retains equality. State values
are exact strings or `DurableWorkState` values; revision values are exact integers.
There is no lossy string coercion. Corrupt/unsupported current row values reject
authority. Production claim construction supplies valid captured facts; manually
constructing a malformed `WorkClaim` is outside the theorem and may raise during
normalization. User-defined equality, hostile module rebinding, descriptor
replacement and concurrent mutation of pure inputs are outside the fragment.

The translator, its integer/microsecond/record lowering, the Python interpreter
and Dafny are trusted toolchain components. Strict basedpyright checking remains
mandatory. A source theorem assumes the adapter supplies the described facts;
the independent native lane checks the adapter and callback path against current
production code. It does not formally verify Django, psycopg or PostgreSQL.

The native matrix runs two/three-row batches, row-wise/set-based settlement,
stale token/state/revision and a persistent callback failure. It records callback
entries and real product writes, checks success-save/finalizer ordering, and
observes group rollback through another PostgreSQL connection. Durable receipts
and independent provider acceptances are observed separately. Rolled-back
callbacks can run again during per-row fallback; these tests do not promise
exactly-once callbacks or physical exactly-once provider effects.

`contracts/python-checks.json` independently enrolls 69 native cases and the
155 existing generated composed-inline application contract cases. The latter include bounded smoke exploration of both evidence-confluence scenarios and
retain their obligation collection/report guards, recovery and fault histories.
The domain-specific callback matrix is an ordinary native test suite, not an
invented generic obligation profile. Removing a test or a generated obligation
cannot silently reduce this required command's coverage.

Source controls remove state/token/revision/key-kind fences, remove the
presence mask, remove delay saturation and shift its exponent. Each must yield
a semantic Dafny failure; an equivalent equality edit must still prove. Native
controls bypass the actual admission plan, collapse token normalization and
suppress persistent callback errors. Fresh processes attest staged current-source
imports before running the unchanged PostgreSQL witnesses. Import errors,
unsupported code, timeouts and setup failures never count as detected mutants.

## Change a contract

Edit the production body and contract together, review the intended requirement,
then regenerate and inspect the generated diff:

```bash
.venv/bin/python -m veripy project generate veripy-project.json --json
.venv/bin/python scripts/check_python_contracts.py
```

An intentional target/function enrollment change additionally requires the
project's explicit `--update-enrollment` option. Review changes to
`contracts/python-checks.json` as changes to required runtime evidence. Never
delete a required case, weaken a postcondition or add an assumption merely to
make a failing implementation green.

The `source_contracts` CI lane includes production modules, proof sidecars,
generated artifacts, adapters, native tests, generated contract fixtures,
execution scripts, compiler patch/dependency inputs and the project manifest.
Its own workflow job is an explicit dependency. Planning failure/full checkpoints
execute it, and the existing validation gate requires its result whenever selected.
