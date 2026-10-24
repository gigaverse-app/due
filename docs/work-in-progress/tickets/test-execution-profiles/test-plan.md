# Test execution policy adoption

CI-esta PR: https://github.com/gigaverse-app/ci-esta/pull/98

## Behavior-preserving extraction

The first commit passes the existing affected-suite map through `legacy`. Existing
executors, service versions, authorization checks and completion gates remain authoritative.
Routing contracts enumerate every true/false combination, unknown scope and fresh manual work.
The contract workflow installs the same immutable CI-esta revision as the planning action.

## Profile validation

Add explicit profiles separately; test unchanged-code probes, restricted effects, wrong occasions,
unknown profiles and unavailable deployment prerequisites. Verify actual selector integration and
workflow conditions in addition to these public CLI contracts. Do not dispatch live probes to
validate routing. Review CI-esta with Aviad before merging its dependency or these consumers.

## Profiles and usage

Both `ci.yml` and `widget-store.yml` accept `profile`, defaulting to `legacy`.
- `thorough`: complete library/integration/type/contracts/wheel validation, including all conformance validators.
- `exhaustive`: the complete inventory, including the widget comparison when dispatched in its workflow.
- `performance`: only the widget comparison suite; dispatch `widget-store.yml` for that work.

Each workflow executes only the jobs it owns. Dispatching CI does not implicitly dispatch the comparison workflow.
For example: `gh workflow run widget-store.yml --ref <branch> -f profile=performance`.
Existing automatic triggers retain legacy selection. Profiles are explicit full checkpoints and do not reuse proof.
No benchmark schedule is added automatically.

Seventy-one public policy cases plus twelve actual workflow-input cases verify all legacy lane combinations,
empty/unknown scope, manual profile overrides and both consumer workflows. Native real-Git routing and existing
suite/validator/gate checks remain in `ci_tests` and CI.

## Shared implementation revision

Planner, routing-contract action and optional budget/resource APIs use CI-esta `f529ac3a0621870d836ec0c7ec7f3b13d46f4df4`.
The shared design and schemas are in https://github.com/gigaverse-app/ci-esta/pull/98.
All adoption PRs remain unmerged pending review of that design.

## Opinionated defaults

CI-esta now owns all standard profiles. The policy retains only the widget comparison expensive/performance tags and the existing PR-occasion exception for thorough. Both workflows and contracts read lane IDs from `.github/test-lanes.json`.

Existing routing expectations are retained; ordinary workflows still default to `legacy` for compatibility. New adopters default to `ci`, which defers expensive/manual work. Overrides replace fields, including empty arrays; `extends` supports genuine exceptions and `defaults: none` disables the catalog.
