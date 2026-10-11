# Prove your application's work tables

Install `due[testing]`. Enroll each production capability once with
`effect_contract_suite(capability, examples=EffectExamples(...))`. The capability's
actual mode, discovery, gate, retry and retention policies determine the required
proofs. Examples supply real commands, provider seams and independent product
observations; they do not implement selection, settlement or recovery.

The complete executable adopter is
[document_examples.py](../tests/conformance/document_examples.py), enrolled in
[test_document_contract.py](../tests/conformance/test_document_contract.py).
It writes real files and observes their contents through the published product
pointer. The replay proof enters the file writer twice and counts the actual
writes. Its failure examples interrupt real storage writes and partial command
admission.

## Configure the host

In the application's test configuration, after Django setup:

```python
from pytest_obligation import configure
from due.django.conformance.host import work_table_host

configure(work_table_host(production_packages={"myapp"}))
```

This binds PostgreSQL transactions, worker connections, clocks, commit and
callback failures, Celery publication failures, and the real Celery soft-limit
exception. Pass `replica_aliases` when the test database configuration does not
identify the application's replicas. Applications with a custom harness host can
compose pytest-obligation's public `django_host` directly.

Initialize the same Celery application, task imports, registry and Beat schedule
that workers use. The schedule proof checks both Django settings and the current
Celery app. A test-only schedule generator cannot substitute for installed
configuration. Applications that intentionally leave routing uninstalled during
tests can use `durable_work_installed(*registrations)`; the optional
`installed_durable_work` fixture requires an application-provided
`durable_work_registrations` fixture. Due never imports an application manifest.

## Declare the evidence your mode needs

Import authoring types from `due.django.conformance.suite`.

| Capability feature | Application examples | Generated evidence |
| --- | --- | --- |
| Every work table | `make_work`, `delivery`, `retry` | Recovery, fencing, lock order, duplicate workers, lost notifications, poison isolation, indexes |
| Finite retry budget | `terminal` | Visible product failure and authorized rearm, or a reason rearm is forbidden |
| Replay-safe effect | `replay` | An actual second external execution leaves the same observable result |
| External operation | `applied_receipt`, `receipt_repair`, `rejections`, `uncertainties` | UNKNOWN blocks resend; receipts repair publication through interruption histories |
| Read-only observation | `observation` | Inconclusive evidence, lost/stale observation delivery, competing receipt, no resend |
| Projection or scheduled action | `snapshot` | Supersession; late projection repair or cancellation after external entry |
| Canonical discovery | `derivation` | Fact-derived obligations, admissible database edge values, poisoned candidates |
| Execution gate | `eligibility` (one or named variants) | Blocked intent, bounded inspection and periodic recovery |
| Independent compensation | `compensation` | Cleanup survives producer exhaustion and late completion |
| Product command | `producers`, `admissions`, `admission_failures` | Commit/rollback, lost wakeup, standalone partial-admission rollback |
| Inline caller | `synchronous_callers` | Recovery after request-path interruptions |
| Concurrent application changes | `in_flight`, `evidence_confluence`, `convergence` | pytest-obligation's shared interleaving and result-application proofs |
| Committed handoff | `handoffs` | Shared commit, callback, broker and external-call interruption histories |
| Actual process death | `process_handoffs` | Shared process-death oracle, with application-owned child entry points |

Use independent observations: product state, provider state, files, or real
boundary call counters. Work-row success alone cannot establish completion of
an application effect. Scoped examples may be context managers, keeping provider
seams alive through the final observation. `environment` scopes ordinary bindings;
producer/admission/inline examples own their scopes so they cannot accidentally
hide an early notification. Process histories own their child setup; fixtures
keep their external resources alive for the whole generated case.

`observation_handoff` reuses an `ObservationExample` for shared handoff histories.
`WorkTableDelivery` captures Celery publication, delivers real task bodies and runs
two registered recovery ticks, advancing through the lease and first retry delay.
It is a bounded history driver, not a guarantee that arbitrary multi-stage
application workflows finish in two ticks. Declare application process histories
or a custom neutral delivery for a different recovery horizon.

`BackgroundWork` is also available for application journey tests: hold/drop
notifications, advance the scheduler clock, drain real workers, and account for
worker errors. It does not run a broker or simulate a killed OS process. The
separate Redis/prefork test and `process_handoffs` cover those boundaries.

## Make omitted evidence fail CI

Due's installed pytest plugin rejects selected suites that were hidden or lost
required methods. Selected proofs cannot pass as skips or expected failures.
Undeclared assessments remain visible debt; use `--due-work-require-assessed` to
reject that debt. Known-gap handling remains owned by pytest-obligation.

```bash
uv run pytest -p due.django.conformance.reporting \
  --due-work-require-assessed \
  --due-work-profile-report=profiles.json \
  --durable-work-report=effects.json \
  --durable-observation-report=observations.json
```

The reports distinguish collected, selected and executed checks, including xdist
runs. Observation reports record names and verdicts, not application values.
Use `assert_complete_enrollment` with an independently supplied production
manifest to catch an entirely deleted suite. Due's example has that inventory
test; the generic plugin cannot infer an application's manifest.

The repository's CI additionally requires all expected application histories to
be collected, selected and passed exactly once. Filtering to one easy test or
retaining only a passing profile report cannot certify the application suite.

## Dependency status

This branch pins an exact pytest-obligation commit that includes recovery
isolation/owed-row proofs and scoped handoff arrangements. The latter is tracked
in [pytest-obligation #49](https://github.com/gigaverse-app/pytest-obligation/pull/49).
Before a PyPI release, use a released pytest-obligation version containing that
change; the current Git dependency is for integration review, not PyPI publication.
