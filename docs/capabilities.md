# Work-table capabilities

Start with a product owner, a concrete work table and one capability. Commit the
product change and captured work through `capability.atomic(owner_id)`. Register
the capability in every process; recovery must run periodically even when all
post-commit notifications appear to succeed. [Complete setup](integration.md).

## Pick the external contract first

| Contract | Public module and type | Executable example |
| --- | --- | --- |
| Repeating captured input is safe | `due.django.public.ReplaySafeEffect` | [Document publication](../examples/documents/effects.py) |
| Several calls need explicit ownership checks | `due.django.public.OwnedDurableWorkCapability` | [Guarded provider calls](../tests/capabilities/test_provider_calls.py) |
| Current desired state must reach an external view | `due.django.capabilities.projection.Projection` | [Projection callbacks](../tests/declarations/test_simple_callbacks.py) |
| A deadline moves or becomes unnecessary | `due.django.capabilities.scheduled.ScheduledAction` | [Scheduled callbacks](../tests/declarations/test_simple_callbacks.py) |
| An occurrence must not be repeated without evidence | `due.django.capabilities.operations.DurableExternalOperation` | [Operation and receipt models](../tests/harness/operation.py) |
| A provider can report the outcome of an uncertain occurrence | `due.django.capabilities.observed_operations.ObservedExternalOperation` | [Observed operation](../tests/harness/observation.py) |
| An output needs independently recoverable cleanup | `due.django.capabilities.compensation.IndependentCompensation` | [Compensation histories](../tests/capabilities/test_compensation.py) |

These are work-table capabilities, including observation and compensation. Due
does not expose a native-row recovery/completion API. The private delivery
arbitration used by observations is an implementation detail.

## Inputs, I/O and settlement

Declare `input_fields` explicitly when the work model also stores mutable retry
progress. A changed input represents a new desired revision; a progress counter
must not accidentally create a new operation. `Capture` maps owner fields into
captured work; supplying both a relation and its `_id` spelling is rejected.

`perform_effect` performs the external call outside the database transaction.
For replay-safe work, `finalize_success` commits the local product consequence
under the captured ownership token and revision. Settlement callbacks must do
only local transactional work. Admit a follow-up obligation in that same
transaction when it is required by the result.

Use `provider_idempotency_key()` where the provider honors an idempotency key.
The immutable admission identity distinguishes a new obligation from an earlier
row whose database primary key was reused; the revision distinguishes targets,
while retries of one target retain the same key.

`Projection.on_projected` and `ScheduledAction.on_completed` are product hooks.
The simple modes retain receipt bookkeeping themselves: callbacks do not need
`super()` to preserve recovery. `DurableProjection` and `DurableScheduledAction`
remain available for richer composition, with their corresponding contracts.

## Uncertainty and evidence

Use `ExternalOperationWork` for non-repeatable occurrences. The runtime records
an attempt before calling the provider. A timeout or worker death can leave an
UNKNOWN outcome; neither lease expiry nor another delivery establishes that
nothing happened.

A canonical Pydantic provider result and a `JsonAppliedEffectEvidenceStore`
allow correlated positive evidence to survive local settlement failure. Recovery
can then repair the product without another external call. Negative evidence
must prove nonapplication and carry the appropriate retry/terminal disposition;
contradictory evidence is rejected. See [receipt histories](../tests/evidence/test_evidence_histories.py)
and [rejection evidence](../tests/capabilities/test_operation_rejection_evidence.py).

For read-only provider queries, subclass `ObservedExternalOperationWork` and
implement `observe_effect` on `ObservedExternalOperation`. Return a correlated
result or `None` when still unknown. Recovery reserves observation deliveries;
a bounded worker performs the provider read. Failed or inconclusive observation
never grants permission to resend. Include `observation_index(...)` on the
concrete table. [Observation recovery tests](../tests/capabilities/test_observations.py).

## Compose existing mechanisms

| Need | Mechanism | Behavior to preserve |
| --- | --- | --- |
| Existing work must reread a fact after another capability settles | `wakes=(Dependent(WorkModel, via=...),)` | The wake shares the settlement transaction; old execution is fenced. |
| Durable owner facts imply missing work | `Discovery` with a bounded horizon or explicit unbounded reason | Recovery derives work from the actual owner selector. |
| New external calls must pause behind a prerequisite | `ExecutionEligibility` | Park execution while evidence repair remains live. |
| An obligation needs another pass | `ContinueAfter` / `ReopenAfter` | Persist the continuation; do not rely on a callback-held message. |
| Product status mirrors work outcomes | `StatusMirror` / `Outcome` | Commit the product consequence with fenced settlement, including late evidence. |
| An output may outlive the worker that produced it | `IndependentCompensation.record_cleanup_before_call` | Persist cleanup intent before crossing the external boundary. |
| A caller needs required completion | `Completion` and completion errors | Express incomplete/stopped work explicitly to the caller. |

See [dependent wakes](../tests/capabilities/test_dependent_wake.py),
[discovery and poisoned rows](../tests/capabilities/test_recovery_deferral.py),
[gates with evidence](../tests/capabilities/test_eligibility_evidence.py),
[continuations](../tests/capabilities/test_continuations.py), and
[completion](../tests/capabilities/test_required_completion.py).

## Test your application boundary

Install `due[testing]` for `due.django.conformance.DurableWorkBindings`.
[The generated contract](../tests/conformance/test_contract.py) binds production
selectors and transitions for recovery, ownership, retention, admission and
retry limits. Its unclaimed profiles describe that fixture's scope.

Use the public fault controls in `due.django.testing` to withhold nudges, expire
leases and interrupt execution. Check both the obligation and the product state.
Library proofs cannot establish your provider's idempotency or whether your
success callback publishes the correct business result.
