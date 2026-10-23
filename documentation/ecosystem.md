---
title: Faster CI and reliable background work
description: Choose pytest-selective, CI-esta, Due, or pytest-obligation for the problem you need to solve.
---

# Choose the tool for your problem

| Your problem | Tool | What you gain |
| --- | --- | --- |
| Every edit reruns unrelated Python tests | [pytest-selective](https://pytest-selective.readthedocs.io/en/latest/) | Explained affected-test selection or complete-lane routing, with conservative fallbacks |
| CI is slow and you need to know why | [CI-esta](https://ci-esta.readthedocs.io/en/latest/) | Job and step reports, actual dependency critical paths, environment caching, and validated evidence reuse |
| Background work can be lost or repeated after a crash | [Due](https://due.readthedocs.io/en/latest/) | Durable Django work tables, recovery, and explicit completion evidence |
| Passing tests miss failures during retries or recovery | [pytest-obligation](https://pytest-obligation.readthedocs.io/en/latest/) | Executable crash histories, fault scenarios, and checks for declared guarantees |

## Faster feedback without dropping required checks

Use pytest-selective to decide which complete lanes an edit affects. Keep docs
and package checks running. Use CI-esta to measure the selected work and find
the dependency chain delaying your required gate. Selection and execution
optimization solve different problems; measure their effects separately.

Follow [the safe lane-routing tutorial](https://pytest-selective.readthedocs.io/en/latest/docs/tutorials/skip-unrelated-ci/)
and [the critical-path tutorial](https://ci-esta.readthedocs.io/en/latest/docs/tutorials/measure-critical-path/).
Both link the measured source runs and explain what their numbers do and do not prove.

## Reliable work with testable guarantees

Due supplies the runtime for recoverable work. pytest-obligation exercises
declared guarantees under failures and recovery. Keep every required conformance
validator in an affected lane; selecting that lane should run its whole contract.
Neither a fast CI run nor an unassessed guarantee establishes production correctness.
