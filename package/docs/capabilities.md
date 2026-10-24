# Work-table capabilities

For the recommended entry points and consolidated imports, read
[which API to use](api-guide.md). Start with the [trivial tutorial](quickstart.md)
if you have not run Due yet.

Start with a product owner, a concrete work table and one capability. Commit the
product change and captured work through `capability.atomic(owner_id)`. Register
the capability in every process; recovery must run periodically even when all
post-commit notifications appear to succeed. [Complete setup](integration.md).

`MyEffect.for_owner(work=MyWork)` infers the owner model from the work table's
declared one-to-one relation and supplies `DurableWorkPolicy()`. Pass `policy=` for
different timing or retention, and optionally `owner=` for an explicit checked
binding. Provider operations that require retained history still need
`DurableWorkPolicy(retention=None)`.

## Pick the external contract first

| Contract | Public module and type | Executable example |
| --- | --- | --- |
| Repeating captured input is safe | `due.django.public.ReplaySafeEffect` | [Document publication](https://github.com/gigaverse-app/due/blob/main/examples/documents/effects.py) |
| Several calls need explicit ownership checks | `due.django.public.OwnedDurableWorkCapability` | [Guarded provider calls](../tests/capabilities/test_provider_calls.py) |
| Current desired state must reach an external view | `due.django.public.Projection` | [Projection callbacks](../tests/declarations/test_simple_callbacks.py) |
| A deadline moves or becomes unnecessary | `due.django.public.ScheduledAction` | [Scheduled publication demo](https://github.com/gigaverse-app/due/blob/main/examples/scheduled/README.md) |
| An occurrence must not be repeated without evidence | `due.django.public.DurableExternalOperation` | [Operation and receipt models](../tests/harness/operation.py) |
| A provider can report the outcome of an uncertain occurrence | `due.django.public.ObservedExternalOperation` | [Observed operation](../tests/harness/observation.py) |
| An output needs independently recoverable cleanup | `due.django.public.IndependentCompensation` | [Compensation histories](../tests/capabilities/test_compensation.py) |

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

A command can wake existing work with `capability.wake(work_id)`. When that
caller will attempt completion inline, use `wake(work_id, notify_worker=False)`
to suppress the worker notification. The same state transition, revision
fencing and recovery fallback still apply; the default continues to notify.
The runnable [catalog refresh command](https://github.com/gigaverse-app/due/blob/main/examples/catalog/README.md)
uses this to restore a missing external index entry without editing its source.
Pair the wake with `complete_or_defer(work)`: it restores a post-commit notification
when an enclosing transaction prevents inline execution.

See [dependent wakes](../tests/capabilities/test_dependent_wake.py),
[discovery and poisoned rows](../tests/capabilities/test_recovery_deferral.py),
[gates with evidence](../tests/capabilities/test_eligibility_evidence.py),
[continuations](../tests/capabilities/test_continuations.py), and
[completion](../tests/capabilities/test_required_completion.py).

## Test your application boundary

Install `django-due[testing]` for `due.django.conformance.DurableWorkBindings`.
[The generated contract](https://github.com/gigaverse-app/due/blob/main/integration_tests/conformance/test_contract.py) binds production
selectors and transitions for recovery, ownership, retention, admission and
retry limits. Its unclaimed profiles describe that fixture's scope.

Use the public fault controls in `due.django.testing` to withhold nudges, expire
leases and interrupt execution. Check both the obligation and the product state.
Library proofs cannot establish your provider's idempotency or whether your
success callback publishes the correct business result.


### Checkpointed projections

A paginated writer can keep its `OwnedDurableWorkCapability` and reuse
`projection_recovery_steps` and `settle_projection_observation` from
`due.django.capabilities.projection`. Wrap each mutating provider call in
`applied_effect_call`; reads alone need no effect attempt. Persist the observation
start before the first page, preserve it across continuations, and settle only
after every page is established. A reopened completed scan starts a new pass.
These are the same repair/retention steps used by `DurableProjection`, not a
separate scheduler. Old UNKNOWN calls remain owed under the declared repair policy.

`projections_with_stale_writes(queryset, window=None)` selects projections with
unresolved evidence from older revisions. It preserves the caller's scope and
uses the same repair predicate as projection settlement; identical current
revision writes cannot overwrite a newer payload. Import it from
`due.django.public` for SQL-backed readiness observations.


### Owners hidden from ordinary readers

A work model can set `work_owner_manager_name = "all_objects"` when cleanup
must retain access to soft-deleted owners. The default is `"objects"`.
Admission and both runtime owner-lock paths use this same manager, preserving
owner-before-work locking and visibility. This changes lifecycle visibility,
not authorization: the application still decides who may request the work.
