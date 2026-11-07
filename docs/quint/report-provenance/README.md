# README report evidence has authenticated provenance

[audit.qnt](audit.qnt) is the **unchanged historical audit model** (`manual_report_dispatch.qnt`) used to express and reproduce this report against [3cc77fe](https://github.com/gigaverse-app/due/tree/3cc77fe0c8eabd2683a447e9b304eb8635a95fac). Its unsafe behavior is intentional; it describes the pre-fix implementation. [fixed.qnt](fixed.qnt) is a separate model of this PR's decision boundary.

Historical model SHA-256: `be078cb594ceb2e76d26c2a602e8131fdd889b0f2da2f72c250d62e86cde7410`.

The reported history is `init → manualRunIdSelected → artifactsDownloaded → updateStandingReadmePr`: complete-looking artifacts from an ineligible revision reach the README update.

## Source mapping

| Model transition / field | Implementation boundary |
| --- | --- |
| `sourceEligible` | Validator checks source/head repository, CI workflow path, scheduled event, main branch, completed status, positive attempt and a unique completed locked job |
| `sameRevision` | Explicit main checkout must match the source run head SHA |
| `authenticateSource`, `rejectSource` | The `tests` admission step runs before either artifact download |
| `bothArtifacts`, `reportsComplete` | Named downloads plus the report action's existing expected-report/schema checks |
| `rerunDuringDownload`, `recheckAttempt`, `rejectRerun` | Attempt-specific job API and post-download expected-attempt check |
| `updateStandingReadmePr` | Renderer and standing README/badge PR update after admission |

Implementation evidence: [workflow](../../../.github/workflows/obligation-report.yml), [validator](../../../scripts/ci/check_report_source.py), [deployed-shell regressions](../../../ci_tests/test_report_source.py).

## Reproduce

Run from this directory with Node.js and npm available. Commands pin Quint **0.33.1** and use the TypeScript backend. `quint test` checks explicit histories; `quint run` samples bounded traces.

```sh
npx --yes @informalsystems/quint@0.33.1 typecheck audit.qnt
npx --yes @informalsystems/quint@0.33.1 typecheck fixed.qnt
npx --yes @informalsystems/quint@0.33.1 test audit.qnt --main manual_report_dispatch --match wrongManualRunCounterexampleTest --max-samples 1 --seed 1 --backend typescript
npx --yes @informalsystems/quint@0.33.1 test fixed.qnt --main report_provenance_fixed --max-samples 1 --seed 1 --backend typescript
```

The historical witness test **passes when the old bug occurs**. To ask for the desired invariant instead, run:

```sh
npx --yes @informalsystems/quint@0.33.1 run audit.qnt --main manual_report_dispatch --invariant readmeProvenanceHonest --max-samples 1000 --max-steps 12 --seed 1 --backend typescript
```

This command is **expected to exit 1 with `Invariant violated`** and a counterexample. It is evidence of the pre-fix bug, not a required passing check for current code. The corresponding fixed check is:

```sh
npx --yes @informalsystems/quint@0.33.1 run fixed.qnt --main report_provenance_fixed --invariant readmeProvenanceHonest --max-samples 1000 --max-steps 12 --seed 1 --backend typescript
```

Executed with Quint 0.33.1: both files typechecked; the historical witness and fixed scenarios passed; the historical invariant produced its expected counterexample; the fixed invariant had no violation in 1,000 sampled traces of at most 12 steps (seed 1).

## Scope

Eligibility and report validity are abstract booleans, not a model of the GitHub API, artifact service, credentials or action internals. The fixed model covers one source and a rerun before the final check; it does not make API checking and publication atomic. Runtime shell tests cover actual metadata predicates, pagination and attempt changes. A live read-only API control separately exercised a scheduled run. Manual sources are deliberately restricted to scheduled CI at the checked-out main SHA.

## Maintained replay and CI

This contract is registered in the `contracts` lane. Install Node 22 and Python 3.14, then run from the repository root:

```sh
npm ci --ignore-scripts
uv run --no-project --python 3.14 --with "pyyaml==6.0.3" python scripts/ci/check_quint_report.py
```

The command typechecks both files, checks the historical expected counterexample and the original `report_provenance_fixed` abstraction, and samples the independent `report_replay` module. It generates seven named ITF histories (seed 1), plus 20 sampled histories (seed 22), and replays them through the actual shell from the workflow with controlled `gh` API responses. The adapter uses the same boundary fixture as `ci_tests/test_report_source.py`: it compares admission, outcome and recheck against model states, and executes the real YAML guard-order regression. Failed contract outcomes remain admissible; pagination and changed attempts have explicit histories. A disposable validator mutant deletes only the scheduled-event guard. The run requires precisely the `bad_event` admission mismatch and every positive history to remain successful; the mutant patch and mismatch are retained.

`test-results/quint-report/` contains the Quint version, seeds, relevant source/model hashes, generated ITF traces, check logs, mutant patch and failure details. CI uploads these even on failure. Missing Quint, empty or missing traces, a generator error, and replay mismatch fail the gate. Do not update model, adapter or boundary independently without understanding any disagreement; preserve the mutation control.

`report_provenance_fixed` is the original broad abstraction; `report_replay` is the separate trace-generating contract linked to implementation. In `report_replay`, `chooseScenario` selects controlled response facts, `admit`/`reject` map to the first validator shell, `recheck` maps to the post-download shell, and `render` means the guards allow reaching the renderer, not that a README or PR was changed. The adapter supplies fixtures for event, SHA, job outcome and pages. It assumes GitHub API responses accurately describe the source and assumes successful artifact downloads; it does not simulate a real GitHub service or verify artifact contents. The ordering assertion checks that both named downloads lie between the guards and the renderer. This does not establish atomicity between the last API check and publication.

The model covers a bounded set of local facts, not all possible API responses, workflow executions or scheduler histories. Random trace sampling and model-to-implementation replay are not exhaustive verification. The separate Due runtime batch and lease models have no actual ORM/transaction replay in this contract.
