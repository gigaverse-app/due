# Conformance API and module boundaries

Start with the [authoring walkthrough](conformance.md). This reference describes
what applications supply, what the harness owns, and where to extend it.
All paths below are relative to `due.django.conformance`. Due is alpha; these are
the intended public imports, not a promise of API stability before 1.0.

## Public imports

| Module | Interface and responsibility |
| --- | --- |
| [`suite`](../src/due/django/conformance/suite.py) | `effect_contract_suite` enrolls a pytest class; `effect_contract` builds a shared `ObligationContract` for advanced composition. Existing example-type imports remain supported. |
| [`examples`](../src/due/django/conformance/examples.py) | `EffectExamples` and the delivery, retry, terminal, eligibility, rejection and discovery declarations below. |
| [`provider`](../src/due/django/conformance/provider.py) | `ProviderProbe`: typed real-call observation, per-identity counters and scoped before/after provider faults. |
| [`binding`](../src/due/django/conformance/binding.py) | `Binding[T]`: a zero-argument factory returning `T` or a context manager yielding `T`. |
| [`host`](../src/due/django/conformance/host.py) | `work_table_host` connects the shared proof engine to Django, PostgreSQL and Celery. Configure after Django setup. |
| [`delivery`](../src/due/django/conformance/delivery.py) | `WorkTableDelivery` drives bounded recovery for shared handoff histories. |
| [`scenarios`](../src/due/django/conformance/scenarios.py) | `ProducerExample`, `AdmissionFailureExample`, `SynchronousCallerExample`: product command boundaries. |
| [`composition`](../src/due/django/conformance/composition.py) | `SupersessionExample`, `ProjectionExample`, `CancellationExample`, `CompensationExample`: changing intent and independent cleanup. |
| [`observations`](../src/due/django/conformance/observations.py) | `ObservationExample`, `observation_handoff`: read-only evidence resolves uncertain operations without resending. |
| [`enrollment`](../src/due/django/conformance/enrollment.py) | `assert_complete_enrollment`: compare collected suites with an independent production capability manifest. |
| [`reporting`](../src/due/django/conformance/reporting.py) | Optional pytest plugin producing effect and observation reports. |

Advanced adapters are public for custom proofs and application journey tests:

| Module | Use it when |
| --- | --- |
| [`bindings`](../src/due/django/conformance/bindings.py) | Building shared profile bindings directly through `DurableWorkBindings`. Prefer suite enrollment for ordinary adopters. |
| [`background`](../src/due/django/conformance/background.py) | Holding/dropping notifications, draining real task bodies, freezing scheduler time and accounting for expected worker failures. |
| [`background_beat`](../src/due/django/conformance/background_beat.py) | Driving selected installed Beat entries with `run_beat`, `tick` and `beat_entries_for`. This does not simulate the Beat daemon. |
| [`interleavings`](../src/due/django/conformance/interleavings.py) | Binding shared ordered actors to Due through `BrokerControl`, `environment` and `connection_scope`. The shared harness owns schedule generation. |
| [`registrations`](../src/due/django/conformance/registrations.py) | Temporarily installing real registrations in tests with `durable_work_installed`. |
| [`database`](../src/due/django/conformance/database.py) | Testing work-table database behavior or keeping an older transaction open for realistic visibility/scale proofs. |

Individual assertion functions already exported by `suite` remain available for
custom proofs. `fact_bindings`, `retry_bindings`, `snapshot_bindings`,
`eligibility_binding`, `projection_retention` and `operation_ambiguity` convert
application examples into the shared profile contracts. `required_examples_for`
reports the mode's base requirements; enrollment also checks policy-dependent
requirements. `failing_external_call(path)` scopes a real dependency failure.
Standalone proof calls do not automatically enroll a capability or establish
complete coverage.

## Example contracts

| Declaration | Required meaning |
| --- | --- |
| `DeliveryExample(work, observe, expected, effect_calls=None)` | `work` was admitted by the real producer. `observe()` reads product/provider state; `expected` states what is owed. `effect_calls()` counts real boundary entries where replay evidence is needed. |
| `FailureExample(attempts, prepare=...)` | `attempts(work)` counts actual attempts, using persisted evidence or an independent provider counter. Optional `prepare(work)` arranges the failure; the runner owns retry execution. |
| `TerminalExample(observe=..., rearm=...)` | Product-visible exhaustion and the authorized rearm command. Supply exactly one of `rearm` or a substantive `no_rearm_because`. |
| `EligibilityExample(delivery, make_eligible, recovery_timeout=...)` | Initially blocked intent; `make_eligible` is a real production readiness transition. Default recovery horizon is five minutes. |
| `RejectionExample(delivery, disposition, expected_attempts=1)` | A provider rejection, its retry/terminal disposition and the expected persisted attempt count after recovery. |
| `FactExamples(...)` | `make_implied`, `move`, `make_stopped` arrange canonical product facts; `desired(work)` observes the derived target. `identity_after_move` describes an identity change. Never recreate the discovery query. |
| `ObservationExample(delivery=..., reveal=..., observation_calls=...)` | Begin with a lost create response; reveal a canonical provider receipt and count actual read-only observation calls. |
| `SupersessionExample(work=..., change=..., observe=...)` | Change desired inputs through a real command and observe the result. `ProjectionExample.latest` adds the latest target; `CancellationExample.completed` identifies the externally completed outcome. |
| `CompensationExample(...)` | A producer work row, the provider seam, late output, the retirement command and an independent `observe()`/`clean` oracle for cleanup. |

The [source declarations for command examples](../src/due/django/conformance/scenarios.py)
document `ProducerExample`, `AdmissionFailureExample.after_save` and
`SynchronousCallerExample`; the [document adopter](../tests/conformance/document_examples.py)
shows real producer and partial-admission bindings.

## Every `EffectExamples` field

These fields are evidence, not alternative runtime implementations. A missing
optional binding can mean **NotAssessed**, or a declaration error when the
capability requires it. Use the generated report to see the actual assessment;
`None` is never a claim that a guarantee passed.

| Fields | What the application supplies |
| --- | --- |
| `make_work`, `delivery`, `retry` | Required fresh producer admission, independent outcome and failing-provider scope. |
| `terminal` | Exhaustion visibility and authorized rearm for finite retry budgets. |
| `replay` | Shared `ReplaySafeEffect` binding that actually enters the external operation twice. |
| `eligibility` | One gate example or a mapping of named variants; keep readiness transitions in production. |
| `immutable_inputs`, `perturb_inputs`, `convergence_not_assessed`, `snapshot` | [Prove fixed inputs by perturbing product facts and revisiting admission](conformance.md#prove-an-immutable-input-declaration), record missing E evidence, or exercise real supersession. |
| `derivation` | Canonical fact arrangement and independent desired-state observation. |
| `applied_receipt`, `receipt_repair` | A canonical applied receipt and a scoped delivery proving recovery repairs product state without repeating the effect. |
| `producers`, `admissions`, `admission_failures` | Named producer/standalone command paths and an interruption after a partial write. These bindings own their scopes. |
| `admission_not_applicable` | A substantive reason there is no partial admission state to roll back; product-plus-work writes require failure evidence. |
| `synchronous_callers` | Named request paths that admit and attempt inline; each binding owns its scope. |
| `rejections`, `uncertainties` | Named provider decisions and ambiguous outcomes; evidence recovery must preserve their meaning. |
| `observation` | Read-only reconciliation of an uncertain external operation. |
| `identity_of` | Stable canonical identity for comparing obligations across generated cases. |
| `in_flight`, `evidence_confluence` | Named shared `Scenario` bindings for concurrent work and conflicting evidence. |
| `convergence`, `convergence_families` | Production result-application binding and explicit `NotApplicable` reasons for individual convergence families. Omitted families remain unassessed. |
| `compensation` | The independent cleanup relationship and its product oracle. |
| `handoffs`, `process_handoffs` | Shared `HandoffHistory` declarations and real child-process histories. Process histories own child initialization. |
| `environment`, `fixtures` | Outer context for ordinary examples, plus pytest fixture names used by generated cases. Provider resources must survive final observation. |

## Ownership and dependency direction

```text
Application tests                     Public authoring API
  production capability -----------> suite.effect_contract_suite
  example factories ---------------> examples.EffectExamples
  provider/resource scopes --------> binding.Binding
                                             |
                                             v
                                 _internal/suite.py
                                  /              \
                     shared profile proofs    Due-specific assertions
                              |                      |
                     pytest-obligation        bindings / delivery
                              \                      /
                               real Due runtime + DB
                                         |
                                  product observation
```

```mermaid
flowchart TB
    App[Application capability and examples] --> API[Public suite facade]
    API --> Engine[Private suite compiler]
    Engine --> Shared[pytest-obligation profiles and fault histories]
    Engine --> Adapters[Due bindings and delivery adapters]
    Shared --> Adapters
    Adapters --> Runtime[Production coordinator and workers]
    Runtime --> Product[Database and external product state]
    Shared --> Oracle[Independent observation]
    Product --> Oracle
```

`examples.py` owns declarations; `suite.py` is an explicit facade over the single
compiler in [`_internal/suite.py`](../src/due/django/conformance/_internal/suite.py).
Do not import `_internal` from application tests. Helpers such as collection,
source-location and report-merge machinery are contributor implementation;
extend through the public declarations and pytest plugin instead. The package
root stays lazy so pytest can load reporting before Django initializes models.

When changing a proof, keep the production transition in its runtime owner and
the portable oracle in pytest-obligation. Add a faulty control that the proof
rejects, then run the [contributor checks](../CONTRIBUTING.md). Reports deliberately
link to the actual proof implementation, even when users import its public facade.

Crash histories also enter `EffectExamples.environment` before `arrange` and keep it active through transition, delivery, recovery and observation. Each history run owns and closes its scope, including faulted runs. Process histories configure their own child-process environment.

Schema index proofs reuse pytest-obligation's PostgreSQL catalog records, including key order, included columns and partial predicates. They reject a B-tree condition on only a later key; an index name or `Index Cond` alone is insufficient evidence of narrowing.

## Derive routine bindings from one provider seam

`seams.ProviderSeam` names the provider environment, real admission, independent
observed/expected outcomes and call counters. `seam_examples(capability, seam,
**overrides)` derives delivery, bounded retry, supported replay and projection
convergence bindings. It never replaces the coordinator with test code.

| Interface | Meaning |
| --- | --- |
| `environment`, `make_work`, `arrange` | Enter the provider fake, admit through production, optionally script the provider after admission |
| `observe`, `delivered` | Independent observed and reviewed expected outcomes |
| `calls`, `calls_for`, `provider_state` | Count actual provider entries; optionally correlate by work identity and observe provider state independently of settlement |
| `refuse` | Configure rejection in place or return a context manager kept active through every retry; required, since every policy derives `retry` from it |
| `outcome`, `rejection` | Scoped outcome/rejection factories with optional admission and observation overrides |
| `producer`, `caller`, `interrupted` | Bind real admission, inline attempts and interruption after a persisted work row |
| `replay`, `convergence` | Reuse the capability's effect method and result application |

Keep producers, gates, derivation facts, uncertainty matrices and distinct
histories explicit. Overrides replace defaults. The declaration requires the same
ARRANGE / REAL PRODUCTION / EXTERNAL SEAM / OBSERVE annotations as handwritten
bindings. Root controls reject constant observations, dead call counters and
refusals that never fail: [executable examples](../tests/conformance/test_provider_seam.py).

The shipped applications now use these derived bindings: [books](../tests/conformance/book_examples.py),
[catalog](../tests/conformance/catalog_examples.py), [documents](../tests/conformance/document_examples.py),
[scheduled publication](../tests/conformance/scheduled_examples.py), and
[print orders](../tests/conformance/order_examples.py). They retain their explicit
producer, terminal, admission-interruption and process-death histories.

`outcome` and `rejection` keep a context manager returned by their script active
through execution and observation. This is how the order application composes
`ProviderProbe.reject()` and reply loss after a real printer submission. Provider
and fault cleanup cannot swallow a proof's assertion. The real book application
has a [positive/mutation control](../tests/conformance/test_application_binding_scopes.py)
that wraps its SQLite provider in such defective cleanup: the working indexer
passes, while a worker that settles a fabricated index key still fails.

`ProviderProbe` and `ProviderSeam` have different jobs: the probe observes and
faults a synchronous provider callable; the seam describes an adopter and derives
its conformance bindings. A seam may use a probe as its provider handle.

`EffectExamples.retry` is required for every policy, and the policy decides which
half of profile J it drives. A bounded policy (`max_rearms`) binds it to
`BoundedRetry` and also requires `terminal`: failing work must exhaust at exactly
its budget and stay terminal. An unbounded policy (`max_rearms=None`) binds the
same failure example to `PersistentRetry`: through six failing executions the work
must reach the provider once each, never become terminal, stay out of due work
until its retry delay elapses (a zero delay makes the next due pass the retry),
and be due again afterwards. A missing retry binding is a declaration error, not
unassessed evidence. [Runtime self-test](../tests/conformance/test_persistent_retry.py).

`interleavings.WorkTableRecovery` runs registered recovery for a tuple of models
and accounts only for worker errors explained by the bound `ProviderControl`.
It reuses the background driver and registration manifest.

Unbounded discovery generates `test_discovery_selection_is_complete`. It compares
the production candidate query with the real per-owner derivation, so an owner
that derivation would enroll cannot silently disappear from recovery. Discovery
with an explicit horizon does not claim to cover every possible owner and does
not receive this check. The [shared proof's scope and limits](https://github.com/gigaverse-app/pytest-obligation/blob/main/docs/selection-completeness.md)
apply; the generated case participates in normal collection and outcome guards.

`process_handoffs` requires an independent `ProcessHistory.expected` value or input-derived callback for every entry. Omitted expectations raise at binding construction; plain pytest-obligation convergence-only histories remain available outside this application binding.


## Existing lifecycle delivery

`due.django.conformance.delivery.SweepDelivery(binding, scope=..., app=...)`
accepts a context-managed `DueWorkSweep` and an optional application registration
scope. Its `scoped(history)` reuses the arranged sweep rather than creating a
second provider environment. Nested histories and failures restore the prior
binding. `rounds` and `round_delay` bound recovery; `recover_with(ticks)` also
executes explicitly declared downstream sweeps. Work-table and existing-lifecycle
delivery share one broker-accounted recovery loop, including delayed deliveries
and refusal to certify a history while scheduled work remains.

`effective_registrations(iterable)` in `conformance.registrations` shares the
ordered test-overlay rule: later names or models replace earlier registrations.
Application adapters that preserve a live manifest's identity can use this
selection before the runtime's normal registration validation. Most standalone
tests should use `durable_work_installed()` directly.

Derived `ProviderSeam` replay proofs check `delivered` as their expected outcome.
If provider observation has a different shape, declare `replay_expected` from
the admitted input, or pass an explicit replay binding with independent `expected`. Equal but wrong provider results must fail.
