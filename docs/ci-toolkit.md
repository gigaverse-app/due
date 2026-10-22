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


## Learn from the tests that ran

The primary test lane retains JUnit and per-file timing hints using CI-esta's
shared `learn-test-timings` action. Artifacts are separate for each dependency,
Python or OS variant. Due records the integration suite; obligation records its
framework-free core matrix. Existing conformance and coverage checks remain.

Download a successful artifact and use `ci-esta-pytest plan --durations
.ci-test-durations.json --test-root tests --shards 4 --exact-files --output-dir
splits`, substituting this project's actual test roots. Unknown files remain in
the plan. These hints affect scheduling only and cannot grant a passing result.
Follow the [timing feedback tutorial](https://github.com/gigaverse-app/ci-esta/blob/main/docs/learned-test-timings.md)
for execution, empty shards and per-service isolation. Compare the existing
parallel strategy before adding hosted runners.


## Keep collection policy explicit

The selector now fingerprints every committed pytest configuration, including
nested package and demo configurations. The real-Git routing regressions prove
that changed or newly added collection policy forces every lane. Review those
settings before refreshing the fingerprints; a fallback is a reason to inspect
the policy, not to remove the guard.
