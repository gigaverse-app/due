# Cutover improvements and their owners

The October 2026 follow-through audited the pre-cutover implementation as well
as the exported library trees. A subtree diff alone misses improvements that
were still in the original runtime, harness or root tests.

The combined runtime/harness inventory covered 85 implementation modules,
803 top-level definitions and 998 named test functions. Definitions and names
were compared first, then changed bodies and renamed controls were inspected.
Equivalent public tests were reused; application-specific inventories retained
their application owner. This is a source and regression audit, not a claim
that every possible history has been explored.

```text
pre-cutover source + tests + tooling
                 |
        compare behavior and owners
                 |
       +---------+------------------+
       |                            |
Due runtime + conformance    pytest-obligation
work-table-specific proofs   generic proofs + integrations + scanners
       |                            |
       +--------- BE adapters ------+
                 |
       existing deployment contracts
       tables / tasks / metrics / tenant context
```

## Changes carried upstream

| Improvement | Owner and regression evidence |
| --- | --- |
| Captured authority has one work-row query | `WorkClaim.owned_work`; coordination, observation and evidence histories |
| Recovery pages share failure isolation and counting | `RecoveryPass.run_page`; `tests/runtime/test_recovery_pass.py` and capability recovery controls |
| Shared claim/retry bindings and fixture attachment | `conformance.bindings`, `conformance._internal.suite`; generated capability and enrollment tests |
| Scheduling an existing obligation preserves its contract | `tests/capabilities/test_scheduled_action_schedule_at.py` |
| Existing task, metric and evidence-table identities can be preserved | [Integration settings](integration.md#migrating-an-existing-application); `tests/runtime/test_deployment_contracts.py` |
| Metric names, label names and allowed values are inspectable without mutating the schema | `runtime.metrics.metric_schemas`; deployment and metric tests |
| Beat cleanup removes only owned or explicitly declared historical task names | `runtime.registry.remove_stale_recovery_schedules`; registry controls |
| Collection does not enroll embedded libraries' own test projects | `due._suite_inventory`; enrollment and collection controls |
| Lazy application hosts, reviewed suite aliases, configured task names, shared row delivery and archive builds | [pytest-obligation's cutover audit](https://github.com/gigaverse-app/pytest-obligation/blob/codex/upstream-cutover-improvements/docs/backend-harness-port.md#cutover-follow-through-october-2026) |

The public module boundary stays unchanged: `due.django.public` is the capability
entry point, `runtime` owns operational integration, and `conformance` owns the
work-table harness. Suite implementation helpers remain in `conformance._internal`; collection
helpers use private `_suite_*` modules. No backend import is required by either public library.

## Controls recovered outside the subtree export

The following controls were missing from the public test tree even though the
behavior they checked had been extracted. They now run in standalone CI:

| Control family | Public test owner |
| --- | --- |
| Census counts, ages, failure states and reset; mutation controls for incorrect aggregation | [`test_database_proof_controls.py`](../tests/conformance/test_database_proof_controls.py) |
| Retention ordering/bounds, concurrent reopening, completion-cutoff recheck, bounded lock cleanup | Same database controls |
| Invalid concurrent indexes, wrong key/include/predicate, unusable predicates, renamed equivalent indexes and alternate bitmap plans | Same database controls |
| Planner settings survive schema probes | Same database controls; generic inspector controls remain in pytest-obligation |
| Database-legal discovery, rejected desired state and poison stopping a discovery page | [`test_discovery_admissibility.py`](../tests/conformance/test_discovery_admissibility.py) |
| Every recovery phase declares a proof and every shared proof names an existing test | [`test_recovery_isolation_phase_proofs.py`](../tests/conformance/test_recovery_isolation_phase_proofs.py) |
| Inline caller histories and unbounded discovery appear in reports and cannot disappear from collection | [`test_conformance_collection_declared_scenarios.py`](../tests/conformance/test_conformance_collection_declared_scenarios.py) |
| An unbounded retry policy refuses a retry example that could never be exercised | [`test_conformance_enrollment.py`](../tests/conformance/test_conformance_enrollment.py) |

Generic crash histories, narrow gap semantics, SQL commit observation, bounded
ownership, eligibility, edge cases, terminal states, source scanners and debt
collection already have public owners. See the linked obligation audit for their
control locations. The newer public tests use framework-neutral reference hosts
and real Django integration tests in place of backend-specific fixtures.

## Deliberate exclusions

`RecoveryLease`, `RecoveryRoute` and `CompletionLease` were unused public-native
API proposals and are deliberately dropped. Observation recovery still uses its
private `runtime._delivery` helper, with the private delivery lease/page tests.
Shared counter arithmetic remains in `runtime.counters`. Generic harness support
for application-owned ORM lifecycles remains in pytest-obligation.

Backend alert routes, concrete Beat entries, tenant wiring, historical frozen
migrations, provider fixtures, reviewed exceptions and application debt are
application policy. Their reusable validators are public; their values do not
belong in a general-purpose library. Existing table names alone do not migrate
schema state: follow the [integration migration guide](integration.md).

## Verification and limits

Run the full Due suite against PostgreSQL and Redis, plus Ruff, Pyrefly and the
isolated wheel check described in [CONTRIBUTING](../CONTRIBUTING.md). The full
suite includes real Celery workers and generated work-table conformance cases.
Run pytest-obligation's core and Django suites and its optional-integration CI
matrix, then exercise both candidates together in the consumer.

The [test plan](work-in-progress/tickets/upstream-cutover-test-plan.md) records
results. Expected failures remain visible; this port does not repair the
consumer's declared domain defects or deploy its cutover.
