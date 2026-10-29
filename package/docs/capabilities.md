# Work-table capabilities

For the recommended entry points and consolidated imports, read
[which API to use](api-guide.md). Start with the [tutorial chapters](tutorial.md)
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

## Let a business row own the work

When a business row already represents exactly one durable obligation, it can
inherit the appropriate work base and bind with `MyEffect.for_work(work=MyRow)`.
It needs no `work_owner_field`, companion row, or missing-companion discovery.
Integer keys remain the default; a UUID primary key works with the same attempts,
worker transport, indexed recovery, and evidence rules.

The producer inserts the business fields and coordination fields in the same
transaction, including through `bulk_create`. Use Due's initial READY state and
valid captured inputs. Register the capability and its recovery schedule before
producing rows. A lost post-commit nudge is recovered from that row; rolling back
the business transaction rolls back the work too. `capability.atomic(pk)` locks
the existing row once for subsequent input changes. It cannot recover business
intent that the producer never committed.

Choose this layout when business retention and obligation retention agree.
Deleting the row deletes the intent and can break recovery of unresolved effects.
Provider operations still require retained work and evidence. Migrating a live
companion table must preserve attempt correlation, receipts, pending progress,
and fencing; changing the Python binding alone is insufficient.

If another engine shares this table during a migration, declare each of its
state choices in `work_external_states`, with the recovery owner and proof for
that state. These declarations appear as exclusions in the generated lifecycle
report and cannot exclude Due states. They do not migrate rows or transfer
ownership. Each engine's selector and generated contract must cover its own
states without claiming the other's rows.

The [UUID business-row example](../tests/harness/self_work.py) and its
[generated failure contract](../tests/bulk/test_self_work_contract.py) exercise
lost notifications, stale workers, provider uncertainty, and late receipts using
the ordinary Due lifecycle.

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


## Bulk operations

A bulk capability sends one fixed list of member keys in as few provider calls
as possible. Each call returns a verdict per member: `COMPLETE` (a definite
final result, including a permanent rejection), `RETRYABLE` (definitely not
applied) or `UNKNOWN` (may have been applied). `due.bulk` owns these verdicts
and their only reducers. Both capabilities create one work row per batch and
keep member progress as bounded JSON on that row; neither creates a row per member.

Choose by whether the provider call is safe to repeat:

| Provider call | Capability | Work model | Uncertain call |
| --- | --- | --- | --- |
| Not safe to repeat (sending email) | `DurableBulkExternalOperation` | `BulkExternalOperationWork`, with inline or domain-owned receipts | Blocks further calls until evidence resolves it |
| Idempotent (adding chat members, upserting users) | `DurableBulkEffect` | `BulkEffectWork` | Raise; the core retry policy resends members without a verdict |

Both take a typed `result_type`, immutable `input_fields` including `bulk_keys`,
and a `bulk_policy` that bounds inline rounds and elapsed time; its backoff
must be shorter than the lease. Exhausting the rounds hands the remaining
members to the core retry policy. Register the capability and admit work
through the normal owned-work transaction or discovery.

| Hook | Application responsibility |
| --- | --- |
| `before_batch(work)` | Recheck product eligibility before each call; return `ContinueAfter` to pause without spending the failure budget |
| `prepare_batch(work, batch)` | Build the request for the captured members with no provider I/O. An exception is a definite nonapplication, never an uncertain call |
| `send_batch(work, batch, payload)` | Make exactly one provider call and return a verdict for every member |
| `progress_updates(work, progress)` | Optionally return application fields to persist on the work row in the same UPDATE as accumulated member progress |
| `finalize_batch(work, result)` | Publish one call's member consequences under the core fence |
| `on_batch_rejected(work, rejection)` | After a retryable whole-call `EffectNotAppliedError`: `RETRY_LATER` (default, spends one retry), `RETRY_INLINE` (paced resend within this claim), or `ContinueAfter` (for example until a rate-limit reset, no budget spent) |
| Ordinary finalizers | Publish uncertainty, exhaustion and permanent failure consequences |

A provider that definitely refuses the whole call raises `EffectNotAppliedError`
from `send_batch`; it never returns every member as `RETRYABLE`, so attempt
records stay truthful. A terminal rejection fails the work.

Use `progress_updates` when the work row also stores product fields derived from
member outcomes. It receives the validated, accumulated progress across all calls,
including earlier completed members. Return a mapping of concrete, non-relation
field names to values; those fields must be outside `input_fields` and Due's
coordination fields. Do not save or perform I/O in this hook. Due writes the
mapping and `bulk_progress` together under the current fence, then calls
`finalize_batch` with the current call's canonical result. A constraint or later
finalizer failure rolls back both. Live delivery and receipt recovery use the same
hook; a stale claim cannot publish the projection. The default returns no extra
fields, preserving existing finalizers.

With an application `published_keys` JSON field on the work model:

```python
def progress_updates(self, work: BatchWork, progress: BulkResult[ItemResult]) -> dict[str, object]:
    return {"published_keys": [item.key for item in progress.items if item.state is BulkOutcomeState.COMPLETE]}
```

Keep updates to other tables and other product consequences in `finalize_batch`.
This hook coalesces a write on the existing row; it does not create another work
table or change the receipt/settlement transaction boundaries.

### Calls that are not safe to repeat

Both bulk capabilities measure the per-invocation elapsed retry budget with a
local monotonic clock. Clock corrections on the database or worker do not extend
or prematurely exhaust that duration. Lease validation, persisted retry deadlines
and attempt timestamps still use database time. The framework-neutral `run_bulk`
runner retains its explicit `now=` datetime clock override for existing callers.

`DurableBulkExternalOperation` extends `DurableExternalOperation`: the ordinary
claim, fenced `WorkExecutionContext`, `EffectAttempt`, retry budget and
recovery path. Due captures an immutable `CapturedBatch` under the work lock,
commits the attempt and membership, then passes that same batch to `prepare_batch`
and `send_batch`. This saves one membership SELECT per live provider round for
both inline and separate receipts. No captured batch escapes a failed commit;
each invocation keeps its own value. `current_batch(work)` remains a validated
database lookup for callers that need to load an open crossing. Recovery still
loads durable evidence. `COMPLETE` members never reenter a later call.
Only definite `RETRYABLE` members may retry; any `UNKNOWN` member blocks another
call until exact-attempt evidence resolves it. Duplicate, foreign and
contradictory receipts are rejected. Partial receipts merge monotonically in
the canonical store and use the same settlement path as a live response.

#### Choose receipt storage

Declare `receipt_model = None` to store captured membership and typed results
on the existing `EffectAttempt` row. This avoids a companion receipt INSERT,
uses the freshly locked attempt for evidence reads, and saves evidence and its
APPLIED verdict together. Work progress and final completion retain their
existing transaction boundaries. This option works with integer and UUID work
keys; it creates no additional work table.

Existing declarations with a concrete `StoredBulkEffectEvidence` subclass
retain their current storage and model hooks. Keep a domain-owned receipt when
it needs custom columns, relations, independent permissions, or model-level
deletion protection. Inline receipts share the attempt's storage, permissions
and deletion lifecycle. Direct ORM/SQL deletion of an attempt deletes its inline
evidence; there is no separate PROTECT relation. Normal provider-operation
retention remains disabled (`retention=None`), and Due's generic attempt
compactor also excludes inline receipts.

An inline receipt has `members`, `result`, and optionally `diagnostics` keys.
Return `BulkResult(items=..., diagnostics=json_value)` to persist provider
metadata in the same locked update and commit as its semantic receipt. `None`
leaves existing diagnostics unchanged. Diagnostics are excluded from canonical
result serialization and never authorize replay or create settlement debt.
If receipt validation or its commit fails, neither the new verdicts nor the
accompanying diagnostics persist; uncertainty still prevents another send.

Use the capability's `record_receipt_diagnostics(attempt_id, json_value)` for
independent metadata observations, including failures without a batch result.
It retains its immediate-write behavior and preserves concurrently accepted semantic evidence
and does not grant replay permission. It is available only with inline storage;
custom receipt models keep their own metadata API. Diagnostics are last-write-wins
and are not used as evidence unless the adopter separately validates a typed
result through `record_applied_effect_evidence`.

Evidence writers may return the canonical result they already read while
persisting it. With `combine_completed_progress = True`, the built-in bulk
writer passes a fully definite committed result into core completion. It avoids
reloading that receipt: definite outcomes for a captured membership cannot
change, and contradictory observations are rejected. Completion still locks
current work and checks its state, token and revision before publishing anything.
Custom writers/preparation hooks, unknown members, and legacy settlement retain
their ordinary read path. Return the merged canonical result, never the unmerged
provider reply, when extending evidence hooks.

The handoff belongs to one invocation and exact returned attempt. Its independent
copy cannot be changed through a provider-owned mutable result. Losing the
process loses only this optimization: registered recovery loads the durable
receipt and uses the same progress reducer and product settlement hooks.

This avoids one receipt read per live bulk crossing. A late fragment arriving
after a partial snapshot may cause an extra progress transaction before final
completion; settlement sees the newer receipt, and accepted members are not
sent again. No transaction boundary, recovery obligation, or retry permission
is removed.

Apply Due's schema migrations before deploying inline writers. The added fields
allow old writers to continue; existing receipts are not automatically moved.
To switch a running capability, quiesce its workers and receipt consumers, copy
every retained receipt (including exact membership, results, pending marker and
needed metadata) to its attempt, verify the copy, and switch the declaration
before resuming. Mixed writers for the same capability are unsupported. To
roll back, quiesce again and copy all new evidence back before restoring the old
declaration. The schema rollback refuses to drop inline columns while any
inline receipt remains; it does not erase evidence to make rollback succeed.

The sparse `effect_attempt_receipt_ix` selects attempts whose inline receipt
still needs settlement. A work row's ready index cannot find late evidence for
an already terminal work row, and the attempt repair index represents a different
debt: unresolved outcomes may need observation without having any new receipt.
The new index is built concurrently; allow temporary disk and I/O capacity.
Co-location makes the attempt row wider and serializes metadata writes with
evidence writers. Measure those costs with realistic receipt sizes before adoption.
#### Completion and recovery

A fully completed batch publishes member progress and final work completion in
one transaction, after the provider receipt has committed independently. Core
completion reloads work and evidence under their locks before applying progress;
an earlier complete-result read only chooses this path. Partial and retryable
rounds still commit progress before another provider call.

This changes failure visibility: if a final completion hook fails, that batch's
progress and product callbacks roll back together with completion. The canonical
receipt remains owed to recovery, which applies it without resending. Previously,
progress could remain visible while the final completion hook failed separately.
Applications whose UI or callbacks rely on that intermediate state must review
this tradeoff before adopting the change. Hooks keep their prior order and state:
`finalize_batch` sees the claimed row, then success hooks see the completed row.

##### Combining the completion write

`DurableBulkExternalOperation` may declare `combine_completed_progress = True`
when its applied hooks can use the in-memory projection. This is off by default.
For a fully completed batch, Due includes `bulk_progress` and `progress_updates`
fields in the existing completion UPDATE, saving one work-row write. Live
execution and receipt recovery use the same preparation and completion functions.
Partial, uncertain and retryable rounds retain their existing progress save.

The opt-in is a hook contract: `on_applied`, `finalize_batch` and their dependent
selectors run before this combined save. They receive current in-memory progress
but must not query or change its projected fields before completion. Model save
overrides, signals and triggers must tolerate one combined write instead of two;
there is no intermediate saved-progress event. Success hooks still run after
the saved projection and `SUCCEEDED` state. Existing adopters keep the original
save-before-applied-hooks behavior unless they explicitly opt in.

Receipt commit, locks, fencing, hook order, retry rules and the rollback boundary
remain unchanged. A failed applied hook, completion write or success hook leaves
the durable receipt available to recovery without another provider call. This
reduces one UPDATE, not one commit. Run the same generated obligation contract
with persisted product and independent provider observers after opting in, and
include `EffectRegression.BULK_PROGRESS` to verify that missing persistence is
actually detected. Ordinary hook-visibility tests complement those generated
proofs.

Extra hooks for this capability:

| Hook | Application responsibility |
| --- | --- |
| `finalize_rejection(work, rejection)` | Publish a whole-call rejection's product consequence once, whether the worker settled it or recovery did |
| `observe_batch(work, attempt, members)` | Look up evidence for an uncertain call (receipt events, a provider task's status); return `None` when nothing is new. Overriding it registers a paged observation recovery step keyed by `observation_key` |

`resume_pending()` can shorten a pending delay, or explicitly reset an exhausted
nonapplication budget with `reset_budget=True`; it refuses uncertain, active
and completed effects, and preserves the same immutable occurrence. The runnable
declarations are `tests/harness/bulk.py`; the email adopter is
`gigaverse/core/mail/reminders/engine/bulk_delivery.py` in the backend.

When transferring definitely unsent legacy work, call
`import_retry_history(work, rearms=..., retry_pending=..., not_before=...)`
inside the owner/admission transaction that excludes the old sender. This can
initialize only an unexecuted READY occurrence. `retry_pending=True` passes the
prior definite failure through the ordinary retry/exhaustion transition, including
the configured product finalizer; `not_before` retains its original wait.
It does not establish nonapplication: the application must first exclude any
possible in-flight old effect. Retain uncertain historical ownership until evidence
resolves it, or use the separate unknown-effect import API.

### Idempotent calls

`DurableBulkEffect` is a `ReplaySafeEffect`-style capability: no attempt or
receipt rows. `prepare_batch(work, keys)` and `send_batch(work, keys, payload)`
receive the members still without a verdict. Each round commits its verdicts to
the work row under the fence before the claim settles, so a replay after a lost
acknowledgement sends only the members still pending. `send_batch` returns
`COMPLETE` or `RETRYABLE` for every key it was given, and raises for anything
uncertain. The runnable declaration is `tests/harness/replay_safe_bulk.py`,
scripted like a chat provider's add-members call: idempotent, all or nothing
except for per-user rejections, and rate limited with a reset time.

`run_bulk()` is the sequencing helper both capabilities use; calling it alone
does not adopt Due's durable lifecycle. The email engine uses the registered
capability for all new sends; only historical receipt settlement remains while
pre-cutover attempts drain. Generated Obligation contracts exercise
both test adopters and the production email adapter, including partial
receipts, rejected-subset retry, delayed receipts across retry turnover and
replay after a lost acknowledgement.

### Bulk provider wait policy

A positive `EffectRejection.retry_after` follows the ordinary durable retry
path, even when `on_batch_rejected` prefers inline retry or a product pause.
It spends one rearm and waits for the greater of the provider floor and the
capability retry delay. This preserves the scalar retry contract and bounds
repeated rate-limit windows. Without a provider floor, the existing inline
round and budget-free `ContinueAfter` policies remain available. Applications
that want unlimited rate-limit waiting need an explicit policy decision rather
than silently bypassing the durable retry budget.

### Loading admission relationships

Declare Django `Prefetch` objects (or ordinary relationship names) through
`for_owner(owner_prefetch=(... ,))` when several relationships are needed to
derive work. For example, `Prefetch("campaign",
queryset=Campaign.objects.select_related("event__community"))` loads that chain
in one additional SELECT instead of three lazy reads.

Due runs this plan after acquiring the owner and existing work locks, for both
explicit admission and discovery. It uses Django's normal relation caches and
does not lock related rows. Related data is a snapshot: if its concurrent
mutation must be serialized with admission, the application must declare and
enforce that locking policy separately. Do not reuse these snapshots across
transaction boundaries. The default empty plan preserves existing behavior.


## Choose integer or UUID work keys

Existing work models keep Django's integer primary key. Choose a native UUID
primary key on a new work model when its identity must be generated outside the
database or shared with an existing UUID domain record:

```python
from uuid import uuid7
from django.db import models
from due.django.public import ExternalOperationWork


class PublicationWork(ExternalOperationWork):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    # Declare the owner, captured inputs and durable_work_schema as usual.
```

A work table chooses one key type. Integer keys remain the compact default;
UUID keys occupy 16 bytes rather than 8 for a bigint, and random UUIDs can have
different index locality. This is identity flexibility, not a performance claim.
Admission still owns row creation and identity: the unsaved input object's
default UUID is not an instruction to reuse an existing row. Reusing a domain
row as the work row is a separate model/lifecycle design.

Runtime APIs accept `WorkId` (`int | UUID`). Registered execution, observation
and deadline messages keep integers unchanged and encode UUIDs as canonical
strings. Workers normalize the wire value through the registered model before
claiming; custom publishers receive the model's native key type.

Effect attempts preserve the existing nullable integer `work_id` column and add
a nullable native `work_uuid` column. Exactly one must be present.
`attempt.work_identity` reads either type. Correlation and recovery use the
matching typed column and its indexes, without casting indexed values to text.
History and repair indexes contain only their matching key family. Model-scoped
queries explicitly select that native key so PostgreSQL can use its partial
index even when a query has no individual work ID. The follow-up index migration
builds replacements concurrently before dropping the old integer indexes;
existing data is retained, and the shared pruning index is unchanged.

Apply the additive migration before deploying code that selects the UUID
column. It builds the new indexes concurrently and validates the key constraint
after installing it without a table scan under the initial constraint lock.
Schema changes still need brief PostgreSQL locks; use the application's normal
migration lock/statement timeout and rollout procedure. Existing integer data
needs no backfill and integer messages remain compatible. Upgrade every worker
and recovery process before admitting UUID work: old code cannot correlate UUID
attempts. Rolling code back is safe only while no UUID work has been enabled.
The schema cannot be downgraded with UUID attempts present because the old
integer key is required; do not delete evidence to force a downgrade.

Generated application contracts support both key types. Keep the product
bindings and full executed report for UUID adopters, including lost delivery,
ambiguous provider acceptance, late receipts and stale claim fencing. The
package's UUID publication example runs the same scalar operation contract
against real PostgreSQL and exercises JSON task transport.

### Sharing a resource across nested executions

`ExecutionResource(factory, validate=None)` owns an explicit keyed resource
scope. Use `with resource.acquire(key) as value:` around a bounded dispatcher
and in its individual work handler. Nested calls for the same key reuse the
outer resource; independent recovery calls acquire and release their own.
The factory remains a normal context manager and owns cleanup on every exit.

This differs from `ExternalService`, whose factory result is cached only for
one admitted execution. Resource reuse is explicit and can span several work
invocations, for example a campaign concurrency slot or a provider session.
It never caches work ownership, eligibility, database rows or provider receipts.
Continue to check changing product state at the original boundaries.

Supply `validate` when a resource can become invalid while its Python scope is
still open. A PostgreSQL advisory-lock holder must reject a replaced or closed
database session before reusing its slot. Different keys, threads, async tasks,
processes and expired copied contexts cannot borrow another owner's resource.
The library does not renew leases or repair a failed factory automatically.

### Reusing a canonical receipt read

Evidence stores may override `record_and_read(attempt, result)` to return the
canonical value they just persisted while the caller holds the attempt lock.
The default calls the existing `record` and `read` methods, so existing custom
stores remain compatible. Bulk receipt ingestion uses the retained merged
value to check completeness without a second SELECT. Composed bulk completion
can also reuse fully definite evidence after commit, as described above. Partial
snapshots still reload because a late receipt may have committed in between.


### Ordered generation and ULID interoperability

The UUID column describes storage, not a requirement to generate UUIDv4.
For new locally generated 128-bit keys, Python 3.14's `uuid.uuid7` gives
time-ordered values without another dependency. Keep externally supplied UUIDs
unchanged. Do not change existing primary keys or fencing tokens as part of
choosing a generator for a new work table. Ordered IDs expose their generation
timestamp, and clock order does not establish causal order across workers.

ULIDs also contain exactly 128 bits. An application using
[python-ulid](https://python-ulid.readthedocs.io/en/stable/) can store them in the
same UUIDField using its lossless `ULID.to_uuid()` conversion:

```python
from ulid import ULID


def new_ulid_key():
    return ULID().to_uuid()


# On a new work model:
# id = models.UUIDField(primary_key=True, default=new_ulid_key, editable=False)
# At the application presentation boundary:
# public_id = str(ULID.from_uuid(work.pk))
```

Due does not require or install that library. The conversion preserves all
16 bytes; the 26-character ULID display string belongs at the application's
boundary. Do not pass that base32 string directly to UUIDField or Due's task
messages, or force a UUID version during conversion: replacing version/variant
bits changes an existing identity. Binary ULIDs and UUIDv7 use the same native
PostgreSQL UUID indexes and comparison operations. A text-key fallback would
add another storage and transport contract without helping either case.

The generated publication contract uses UUIDv7. Focused lifecycle, late-evidence,
deadline and real-worker tests run with UUIDv4, UUIDv7 and ULID-layout values. This proves
the lifecycle does not depend on UUID version bits; it does not certify a
third-party generator or promise a global generation order.


### Attempt IDs

Due generates new EffectAttempt primary keys with Python's UUIDv7 generator.
Existing UUIDv4 attempts and their receipt foreign keys remain unchanged and
readable by either version of the code. The migration changes the Python
default only; it does not rewrite persisted IDs. Fencing tokens remain random
UUIDv4 values. Evidence correlation still uses the exact attempt ID, claim and
desired revision; UUID timestamp order never grants authority or resolves
ambiguous effects. Attempt IDs now expose an approximate generation timestamp.
