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
| Lazy application hosts, reviewed suite aliases, configured task names, shared row delivery and archive builds | [pytest-obligation's cutover audit](https://github.com/gigaverse-app/pytest-obligation/blob/60ba188/docs/backend-harness-port.md#cutover-follow-through-october-2026) |

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

The final behavioral comparison also recovered race timeout validation and
stalled-transaction cleanup. PostgreSQL lock timeouts must not round to zero;
a racer still holding a transaction when the proof fails must release its locks.
These controls now live in pytest-obligation's core and Django race tests. Its
`integrations.django.worker_connections` is the shared connection owner; Due's
existing `conformance.worker_connection` imports remain compatibility exports.

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

## Second pass: changes concurrent with cutover

The first audit's merged source predated backend #5361 (`31c25aa13`). That
consolidation is now carried into the public owners too. Due shares captured
outcome/error finalizers and beneficiary affinity across settlement paths; the
expired-claim reaper uses `RecoveryPass.run_page` with its original clock,
selection, transactions, deferrals and accounting.

Projection and scheduled recovery now share the private
`capabilities._attempt_recovery` sequence:

```text
expire uncertainty -> derive owed work -> resolve attempts -> prune receipts
                          |
                 projection late-write repair
```

Projection still publishes its census after the sequence. Scheduled actions
still preserve the started-attempt witness that prevents duplicate admission.
Retention computes its cutoff when the phase runs, not when the steps are built.
The integrated standalone suite, including the latest scheduled-publication
merge, passes: 1,461 tests and four existing expected failures. The generic case/binding consolidation and SQL-normalization fix are
accounted for in pytest-obligation's second-pass audit.


The merge with scheduled publication exercised the recovered phase-exhaustiveness
control: its new deadline-wakeup phase lacked a proof registry entry. It now
names the existing real broker-failure test, which proves due work and other
hints still progress. Proof factories also share obligation's guarded scope:
adopter cleanup cannot swallow a failed delivery verdict. See the
[proof resource test plan](work-in-progress/tickets/proof-resource-ownership-test-plan.md).

## Third pass: exercise the public bindings in every application

The five runnable applications now use `ProviderSeam` for all six capability
bindings. Real provider refusals and reply loss exposed discarded context
managers in the derived retry/outcome factories. Those scopes now remain active
through the proof and preserve failures during cleanup. Explicit admissions,
terminal behavior, cancellation and process-death histories remain alongside the
derived proofs. See the [application evidence matrix](../examples/README.md#executable-provider-bindings).

The repeated source audit covered the current pre-cutover runtime/harness and
adjacent session tooling. No additional runtime behavior was missing; the
fixture-resolution safeguard found outside the harness was ported to
pytest-obligation, and this repository's CI enables it. The paired suite runs
against that reviewed candidate. The [obligation audit](https://github.com/gigaverse-app/pytest-obligation/pull/58)
records the remaining differences, scoped lifecycle findings and control owners.

Consumer consolidation found an additional boundary issue: the generic
`transaction_in_progress` helper lived beside Due's test model. It now has one
model-free owner in `pytest_obligation.integrations.django.transactions`, with a
bare import alias here for compatibility. BE also uses the public worker scopes,
lock recorder and Beat driver instead of its remaining implementation copies.
The public lock ledger is shared, and missing scope/SQL controls now run upstream.


## Follow-up against newly merged backend behavior

A later main-branch merge introduced an actual recording-lifecycle consumer of
`CompletionLease`. The earlier decision to drop unused native APIs did not cover
this newly shipped use. The focused [completion API](completion-leases.md) and
its database/cancellation histories are now public; the unused native runtime
facades remain removed. BE consumes this public owner.

The repeated audit also removed a dead BE suite-inventory copy and consolidated
registration overlay selection and existing-lifecycle delivery. `SweepDelivery`
shares work-table recovery's bounded loop and downstream ticks. Provider-seam
replay now forwards the independent expected outcome introduced by the latest
obligation contracts. A real SQLite book-provider corruption control failed
before that forwarding fix and passes only when both replay proofs reject the
wrong content. File and book process-death histories from main remain connected.
