# Widget profiler performance test plan

Preserve every application sample, browser crash journey, CPU repeat, wall capture,
thread, prefork child, restart generation and existing quality gate.

- [x] Compare flat capture with Yappi 1.7.6 public statistics on the same stopped real CPU/wall profile, including main and worker threads and builtins.
- [x] Run existing real subprocess/fork tests and all unusable-capture rejection cases.
- [x] Measure old/new snapshot collectors on the same real captured function population.
- [x] Run the complete hosted widget matrix and compare actual profile-step timing and quality artifacts against the baseline.
- [x] Run lint and type checks; retain all existing benchmark commands and samples.

Completed in [Due #72](https://github.com/gigaverse-app/due/pull/72).
All ten capture artifacts and six quality reports were inspected. See the
[measurement and limits](../../demo-comparisons.md#ci-runtime-measurement).
