# Widget profiler performance test plan

Preserve every application sample, browser crash journey, CPU repeat, wall capture,
thread, prefork child, restart generation and existing quality gate.

- [x] Compare flat capture with Yappi 1.7.6 public statistics on the same stopped real CPU/wall profile, including main and worker threads and builtins.
- [x] Run existing real subprocess/fork tests and all unusable-capture rejection cases.
- [x] Measure old/new snapshot collectors on the same real captured function population.
- [ ] Run the complete hosted widget matrix and compare actual profile-step timing and quality artifacts against the baseline.
- [ ] Run lint and type checks; retain all existing benchmark commands and samples.
