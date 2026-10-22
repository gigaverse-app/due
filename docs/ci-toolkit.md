# CI selection, caching and critical paths

This repository uses pytest-selective to decide which complete validation lanes
are affected. Routing regressions and metadata checks always run. An aggregate
`Validation gate` checks every prerequisite and accepts a skipped runtime lane
only when the planner explicitly returned boolean `false` for its inputs.

The completed-run CI-esta observer reads that stable gate, rather than optional
matrix cells. It computes the observed dependency chain, execution-only critical
path and execution slack from the source workflow's `needs` graph. Its separate
job cannot delay validation. The report distinguishes execution time from delays
after prerequisites; those delays are not all runner queue time.

Ordinary environment setup uses the shared exact `.venv` cache. The key includes
Python, platform, workspace manifests and install groups. Restores are checked
and followed by locked synchronization; unusable environments are rebuilt.
Specialized upstream application setup remains in its owning lane. Cache and
selection results are separate measurements, not interchangeable savings.

First-attempt PR pushes supersede earlier work on that PR. Main, manual runs and
explicit reruns retain independent concurrency groups. Full main/manual and
`test:full` validation remain available. Required conformance validators and
package-only compatibility checks remain attached to their lanes.

The unconditional CI job runs `ci-esta check-workflow` against this workflow and
lane configuration, so renamed lanes or omitted metadata prerequisites cannot
silently change what the aggregate gate means.

For adoption in another repository, follow CI-esta's
[optimization guide](https://github.com/gigaverse-app/ci-esta/blob/main/docs/ci-optimization.md),
[cache tutorial](https://github.com/gigaverse-app/ci-esta/blob/main/docs/environment-cache.md),
[selected-gate contract](https://github.com/gigaverse-app/ci-esta/blob/main/docs/selected-gates.md)
and [critical-path guide](https://github.com/gigaverse-app/ci-esta/blob/main/docs/critical-path.md).
Compare matched successful runs, record cold/warm cache states, and retain raw
run identities before making a numerical performance claim.
