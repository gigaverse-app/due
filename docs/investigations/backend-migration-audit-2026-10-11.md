# Backend migration audit — 2026-10-11

For current package support and follow-through, see the [backend adoption guide](../../package/docs/backend-adoption.md). The findings below describe the pinned revisions, not the latest release.

**Historical verdict at the pinned audit revisions: not ready to replace BE's embedded primitive.** The mature work-table
runtime is substantially ported. Import availability and the standalone library
suite do not establish safe schema transfer, equivalent application fault handling,
or preservation of the backend's harness guarantees.

Audited on 2026-10-11 against backend `04786fb2d0c0027526785c415178d0486d9dc989`
and Due `65e0bcf` (merged PR #5). Due's locked pytest-obligation source is
`0992f189a55451946e0674ac9f6abfb0a50c3648`; its PR #49 is merged, but the latest
GitHub release at audit time remains v0.8.0. This is a source and isolated-boundary
audit, **not a completed backend migration rehearsal**. No BE source, deployment,
or production data was changed.

Concurrent [Due PR #9](https://github.com/gigaverse-app/due/pull/9) adds Beat
and interleaving adapters. It was discovered while finishing this audit and is
not part of the pinned baseline or the validation results below; reassess the
affected harness integration after it lands.

## Evidence and its limits

| Check | Result | What it establishes |
| --- | --- | --- |
| Existing consumer-import inventory, rerun | 305 available import statements, 120 distinct symbols; 3 native-counter imports and 2 BE broker-fixture imports separately classified | Names resolve. It deliberately excludes the shared harness, and does not check signatures, type identity, configuration strings or behavior. |
| Runtime comparison after normalizing the package prefix | Every non-native runtime module has a destination; significant differences include application hooks, exception identity, model ownership, metrics and schedule cleanup | No missing runtime module in this scope; not behavioral equivalence. |
| Actual BE and public pytest option registration bodies | Both duplicate-option failures reproduced | Installing the public pytest plugin without adapting BE fails before collection. |
| Due-generated PostgreSQL DDL with BE's existing index name | `DuplicateTable: relation "effect_attempt_work_ix" already exists` | A naive parallel-table installation fails. The probe used an isolated schema; rollback and schema removal were verified. |
| Due `ends_the_tick` with the actual BE exception class | BE `NotInstalledError`: false; Due `NotInstalledError`: true | The fatal-configuration-error contract changed across the package boundary. |
| BE disposition class passed into Due's real contract builder | `ObligationContractDesignError`: convergence family needs `NotApplicable` | Matching field names do not make embedded and public contract types interchangeable. |
| Public `HandoffHistory` constructed with BE's `expected` field | Pydantic `extra_forbidden` | Mechanical import rewriting cannot preserve the current handoff declarations. |
| All 17 BE adopter suites against Due | **Not run** | No application replacement verdict can be inferred. |

The prior Due run reported 821 passing tests and 64 generated document-example
cases ([CI run](https://github.com/gigaverse-app/due/actions/runs/38100864474)).
Those are standalone evidence from the prior parity PR, not new BE migration
results. No complete test suite was rerun for this documentation audit.

Reproduce the name inventory from the Due checkout:

```bash
uv run python -m scripts.check_backend_compatibility /path/to/gigaverse-backend \
  --report /tmp/due-backend-imports.json
```

## Findings and ownership

### M1 — Evidence-table installation conflicts and ownership transfer are unresolved

**Cutover blocker; BE migration design, with a Due-supported adoption path.**
BE's `core_effectattempt` and `core_recoverydeferral` are concrete `core` models.
Due's initial migration creates `due_effectattempt` and `due_recoverydeferral`
using the same explicit index names. PostgreSQL index names are schema-wide;
adding Due's app and running its migration alongside BE's schema fails. Renaming
indexes to make that operation pass would still leave a more serious problem:
new empty evidence tables cannot represent earlier unknown provider calls.

Three production receipt models inherit a protected one-to-one relation to the
attempt table: `SocialPublishReceipt`, `MediaLiveCreateReceipt`, and
`ViewOnlyEgressReceipt`. Preserve their UUIDs and foreign keys, as well as
`work_model`, `work_id`, `claim_token`, `desired_revision`, outcome/rejection,
observation times and pending-repair state. Preserve deferral identities and
backoff too; these are scheduling state, rather than provider evidence.

Keep application work tables, app labels, primary keys and admission timestamps
stable: provider idempotency keys and evidence lookups depend on them. Due's
abstract tenant field changes from BE's 16-character choices to an unconstrained
64-character string. Decide explicitly whether a BE-owned abstract base retains
the old field or a reviewed field migration changes it.

**Acceptance:** replay the complete migration graph from an empty database and
from a populated pre-cutover database. Seed UNKNOWN, APPLIED-but-unpublished,
rejected, retrying, queued and deferred work; verify exact identities, receipt FK
integrity, constraints, indexes, idempotency keys and post-migration recovery.
`makemigrations --check` alone cannot prove this. Keep the frozen
`durable_effect_schema_v1` helper and historical migrations importable.

Sources: [BE models][be-models], [receipt base][be-receipts],
[Due initial migration](../../package/src/due/django/migrations/0001_initial.py).

### M2 — Application wiring errors no longer abort the recovery tick

**Package integration gap; Due and BE.** Due copied the installation-slot utility
into `due._installed`. Its `NotInstalledError` is now a different class from
`gigaverse.core.shared.installed.NotInstalledError`. `ends_the_tick` checks the
Due class, so a missing BE service raised by discovery or a finalizer may be
isolated as an identity failure instead of aborting the tick as BE requires.
The probe establishes the classifier mismatch; it did not execute all affected
application workflows.

Provide an explicit supported way for the application to declare fatal wiring
errors, and install it centrally. Do not fix this by catching deployment defects
as ordinary work failures or importing a private Due class throughout BE.
Also adapt BE's wiring-isolation snapshot: it currently enumerates BE's own
`all_slots()` weak registry, which cannot see Due's registration slot.

**Acceptance:** inject the actual BE error into a production recovery phase;
assert immediate abort, no subsequent identity execution, correct reporting,
and unchanged owed evidence. Verify a test cannot leak Due registrations into
the next test. Keep positive controls for ordinary isolated row failures.

Sources: [BE installed slots][be-installed], [wiring snapshot][be-wiring-state],
[Due recovery classifier](../../package/src/due/django/runtime/recovery_pass.py),
[Due slots](../../package/src/due/_installed.py).

### M3 — The public pytest plugin cannot simply coexist with BE's current bootstrap

**Collection blocker; BE test integration.** The root conftest and the public
plugin both register `--due-work-explore`. BE's optional work-table reporting
plugin and the public plugin both register `--due-work-profile-report`. Executing
the actual option-registration functions and parsing reproduced both conflicts.
BE accepts exploration mode `extended`; the public plugin uses `deep`.

Choose one owner for each option, profile report and collection/outcome hook.
Due autoloads its own work-table collection guard; BE also loads
`session.durable_suites`. Their overlap needs an explicit migration, including
the BE native suites that must remain enforced. Preserve the disappeared-suite,
skip/xfail, selected-proof and xdist-merge controls rather than disabling guards
until pytest starts. Configure a public host with `production_packages={"gigaverse"}`
and explicit replica aliases. Keep BE lock-order consumers and Due-generated
proofs on the same ledger where cross-workflow comparisons are required.

**Acceptance:** full collection with the normal BE plugins; 17 registered suites
accounted for; expected = collected = selected for the selected migration lane;
report outcomes include every mandatory proof. Deleted/ignored suite, skipped
proof, xfailed mandatory proof and filtered-lane negative controls must fail.
Native reports must retain their previous assessment and execution coverage.

Sources: [BE conftest][be-conftest], [BE reporting][be-reporting],
[BE collection guard][be-collection], [Due plugin](../../package/src/due/pytest_plugin.py),
[Due host](../../package/src/due/django/conformance/host.py).

### M4 — Adopter bindings require semantic adaptation, beyond work-table imports

**Collection and proof-preservation blocker; BE bindings and pytest-obligation.**
Adopters also import embedded `NotApplicable`, `ConvergenceFamily`, replay
bindings, interleaving scenarios, external-call declarations and handoff histories.
These imports are outside the earlier work-table mapping. Due's contract builder
rejects an embedded `NotApplicable` instance even though it has the same fields.
Public bindings also use validated Pydantic types where BE uses dataclasses;
constructor and copy conventions need checking individually.

MediaLive's `PROVISIONING_HANDOFF`, `RETIREMENT_HANDOFF` and `DELETION_HANDOFF`
explicitly declare the required product outcome. The public `HandoffHistory`
refuses `expected`, and has no `recovery_ticks` field. **Do not delete these fields
to get collection green:** the expected outcome protects against delivered and
recovered histories agreeing on the same wrong result. Preserve that independent
oracle through a supported public declaration or adapter, including its negative
control. Keep application callbacks and provider fakes as the actual bindings.

**Acceptance:** convert every directly or transitively used binding; compare
profile/family dispositions and generated histories before/after. Deliberately
break receipt settlement, handoff creation and provider replay permission; the
corresponding generated proof must fail with its expected invariant.

Sources: [MediaLive declarations][be-medialive-contract],
[MediaLive histories][be-medialive-histories],
[Due contract builder](../../package/src/due/django/conformance/suite.py).

### M5 — The handoff driver has a smaller recovery contract

**Harness gap requiring validation or extension in Due.** BE's handoff runner
performs bounded recovery rounds, advances beyond pending message ETAs, permits
explicit downstream recovery ticks and rejects a history that still has held
messages. It also lifts task-instance dispatch recorders while its real test
broker owns delivery.

Due's `WorkTableDelivery` runs the selected registration twice, advancing its
lease and first retry delay. It has no downstream tick list, no ETA-draining
round loop and no equivalent final held-message assertion. Its background broker
patches class-level publication methods; it does not reproduce the handoff
runner's explicit lifting of instance recorders. This is a difference in proof
scope, not evidence that every BE history necessarily fails.

MediaLive's application interleaving recovery deliberately visits input, channel
and teardown registrations. A notification that successfully reaches a successor
is not a substitute for proving recovery after that notification is lost.

**Acceptance:** exercise these real cross-capability histories, including a lost
successor wake, provider completion after caller failure, delayed retries and
cleanup after retirement. Recovery must run only declared production entry points,
use a bounded declared horizon and fail if owed follow-up work is left unobserved.

Sources: [BE handoff runner][be-handoff], [MediaLive interleavings][be-medialive-races],
[Due delivery](../../package/src/due/django/conformance/delivery.py).

### M6 — Some harness proof controls and query-scale evidence remain unported

**Evidence gap; Due.** A test-name inventory found 27 BE non-native primitive test
names without an identical name in Due. That is a review queue, not 27 missing
behaviors: operation recovery isolation, for example, is consolidated into
`test_recovery_preserves_owed_evidence_and_isolates_only_identity_failures`.
Application-specific alert and frozen-schema tests belong in BE.

The following specific controls were not found in the current Due test tree:

- Gate proof controls that disable the runtime fallback or hide writes behind an
  unchanged snapshot (`test_conformance_enrollment.py` in BE).
- Generated dependent-wake proof acceptance and mutation rejection in
  `test_dependent_wake.py`.
- Shared receipt-repair rejection of broken settlement, simple-callback race
  proof controls, and rejection-proof detection of lost recovery policy.
- The shared `test_retained_history_does_not_drive_shared_queries` suite, which
  lives outside BE's primitive test directory. Migrated index-existence checks
  do not establish bounded reads over retained work/evidence history.

Map each assertion to an equivalent existing test or port the missing control;
do not count a renamed test as a loss, or an unrelated passing runtime test as
proof that a generated oracle catches a defect. Keep shared query-scale fixtures
library-owned rather than copying a large fixture into every adopter.

Sources: [gate controls][be-enrollment], [wake controls][be-wake-tests],
[receipt controls][be-mirror-tests], [race controls][be-callback-tests],
[rejection control][be-rejection-tests], [retained-history proof][be-scale].

### M7 — Process hooks, queued messages and persisted schedules need a cutover bridge

**Operational compatibility blocker; BE.** Bind `ApplicationHooks.tenant_scope`
to BE's universe scope and `pin_after_write` to its replica-routing pin callback.
Install these before capabilities are used, and install the same application
manifest in request, worker, Beat and management-command entry points. Due's
standalone defaults do not establish BE tenancy or read-after-write behavior.

All three task names change namespace: execution, recovery and observation.
Retain aliases that call the canonical Due task bodies, or prove old messages
and persisted schedules cannot arrive. Preserve registration names, work IDs,
captured delivery tokens and revision fences; do not re-admit old payloads.
Preserve dedicated `social_sharing` and `watch_access_export` queues, configured
cadences, and execution soft/hard limits. Avoid duplicate Beat owners.

BE cleans stale persisted recovery schedules by the historical task-name suffix.
Due intentionally cleans only its exact current task path. Consequently old
persisted entries, including removed registrations under older paths, need a
BE-owned cleanup/translation step. An alias alone does not make an obsolete
registration valid.

**Acceptance:** start each entry point; route a captured old execution message
and an old observation token through new workers; simulate overlap with a queued
new delivery; recover with all nudges dropped. Seed old PeriodicTask rows and
prove one live schedule per registration with obsolete rows retired. Test
universe restoration and actual primary/replica routing under concurrent work.

Sources: [BE wiring][be-wiring], [BE task registry][be-registry],
[Due hooks](../../package/src/due/django/integration.py),
[Due schedule cleanup](../../package/src/due/django/runtime/registry.py).

### M8 — Operational evidence and native consumers must retain their owners

**Release gate; BE and package release.** Existing Prometheus alerts query
`durable_effect_*`; Due emits `due_*`. The recovery task-crash log rule names the
old Celery path. Update queries, task matching and dashboards with the deployment;
a renamed series must not make a broken scheduler look quiet. Preserve
`durable_work_status` command compatibility or migrate its operational callers
explicitly; Due's command is `due_status`.

Keep native lifecycle code and native contracts backend-owned. The three native
counter imports are intentional non-port scope; this is not permission to delete
the entire embedded subtree. Keep BE's Redis fixture isolation, model-hook wiring
and historical migration helpers. Update evidence-boundary and source-enrollment
scanners to recognize the canonical new imports without weakening their policies.

For normal release consumption, publish/use a pytest-obligation version containing
the required post-v0.8.0 functionality and scoped arrangements. Due currently
pins a Git commit in its testing extra; a merged upstream PR is not a released
dependency. Python 3.14 and Django are compatible with BE's declared baseline
(BE locks Django 6.0.8), but a combined dependency resolution remains untested.

**Acceptance:** installed-wheel BE smoke and lock resolution; alert-expression
and crash-rule tests; seeded failed/stalled/poisoned recovery appears in the
operator command and expected telemetry. Native and migration-history suites
remain green.

Sources: [BE alerts][be-alerts], [BE evidence boundary][be-evidence-boundary],
[Due dependencies](../../package/pyproject.toml).

## The 17-adopter migration acceptance matrix

Every row below is **not yet executed against Due**. These are existing application
suites, not new library examples. Preserve their production capability objects,
entry points, provider fakes and product assertions while adapting the harness.
All paths below are relative to `gigaverse/core/` in the pinned BE source.

| Registration | Existing suite | Distinct migration evidence to preserve |
| --- | --- | --- |
| `recording_recap` | `streams/recording/tests/test_recording_recap_contract.py` | Submission identity, Prefect eligibility, lost admission notification |
| `event_cancellation` | `community/scheduled_events/tests/test_event_cancellation_due_work_contract.py` | Multi-step cancellation and visible completion, inline caller, limits |
| `community_picture_rendition_generation` | `community/tests/test_community_rendition_contracts.py` | Captured picture revisions, stale output, reserved cleanup |
| `community_background_rendition_generation` | same | Background revision and publication identity |
| `community_picture_rendition_cleanup` | same | Owed cleanup survives producer failure |
| `community_background_rendition_cleanup` | same | Background-object cleanup and completion |
| `community_picture_rendition_upload_cleanup` | same | Uploaded output protected before publication |
| `community_background_rendition_upload_cleanup` | same | Independent upload-cleanup obligation |
| `profile_rendition_upload_cleanup` | `account/tests/test_durable_rendition_upload_cleanup.py` | Lost cleanup delivery and replay-safe deletion |
| `profile_rendition_generation` | `account/tests/test_profile_picture_renditions_due_work_contract.py` | Revision races, output ownership, compensation |
| `profile_rendition_cleanup` | `account/tests/test_durable_rendition_cleanup.py` | Snapshot identity and safe replay |
| `medialive_input` | `streams/medialive/tests/test_medialive_creation_contract.py` | UNKNOWN cannot resend, input receipt, channel handoff and teardown wake |
| `medialive_channel` | same | Captured input, canonical receipt, observation publication |
| `medialive_teardown` | `streams/medialive/tests/test_durable_medialive_teardown.py` | Retired generation, late provider evidence and multi-resource cleanup |
| `view_only_egress` | `streams/view_only/tests/test_view_only_work_contract.py` | Observation without a second create, exact provider/product result |
| `social_publication` | `social_sharing/tests/test_dispatch_retries_due_work_contract.py` | Non-repeatable send, late receipt, terminal result and dedicated queue |
| `watch_access_export` | `payments/watch_access_export/tests/test_durability.py` | Buyer readiness gate, expiring fan-out rollback, exported object and purchase outcome |

The registry is authoritative ([manifest][be-manifest]). Run product journey tests
beside these generated suites: a green work row alone is not proof of the complete
application workflow. Produce before/after profile, effect and observation reports
and explain every removed or newly unassessed case.

## What “full pytest-obligation use” means here

It means retaining the guarantees the adopters actually need, not turning on every
optional feature or claiming all A–J profiles for every capability.

| Capability | Current Due evidence | Migration audit requirement |
| --- | --- | --- |
| Generated mode requirements, production bindings, poison isolation, concurrency, indexes and mandatory collection | Ported and exercised by standalone tests | Run actual adopter bindings and original mutation controls; index checks do not replace the missing scale proof |
| Commit/callback/publication faults and external-call uncertainty | Public host and handoff integration exist | Adapt BE histories with their independent required outcomes and complete recovery path (M4–M5) |
| Real subprocess death | Document example has real process-death cases | This does not certify BE worker bootstrap, routing, DB ownership or provider histories |
| E in-flight and evidence-confluence families | `EffectExamples` forwards these to the public contract | No real application interleaving declaration was found in Due's current tests; migrate BE's actual scenarios |
| Hypothesis schedule exploration | Dependency available; CLI supported | Current Due CI does not enable exploration. Run relevant BE scenarios with `--due-work-explore=smoke`, retain replay traces and use `deep` where warranted |
| Assessment and evidence reports | Due CI requires assessed profiles and gates its document example | Gate all 17 BE registrations with explicit dispositions and no silent proof loss |
| Receiver/hook faults | Public host supports an optional breaker; Due's convenience host does not bind one | Assess only workflows that rely on such receivers/hooks; do not claim this fault family from default host configuration |

## Migration sequence and stop conditions

1. **Close package and harness gaps.** Resolve M2 and establish equivalent M4–M6
   proof contracts. Publish the required test dependency. No BE runtime cutover
   is needed for these independently testable changes.
2. **Prepare BE integration in isolation.** Adapt imports, plugin ownership,
   fixtures, shared lock ledger, host, fatal-error and application hooks. Bind the
   existing 17 suites. Establish their before/after inventory and mutation controls.
3. **Rehearse database adoption.** Design explicit migration-state and database
   operations for one canonical attempt/deferral store, including receipt relations
   and schema-wide names. Verify fresh install, populated upgrade and the defined
   rollback path. Do not use `--fake` as evidence of compatibility.
4. **Prepare a bounded cutover.** Install compatible task aliases and schedule
   translation; choose either a tested mixed-version window against one canonical
   store or a controlled pause/drain of affected writers and workers. Two independent
   evidence stores must never authorize the same provider occurrence. Resume only
   after migrations, registration wiring and read-only status checks pass.
5. **Prove deployment behavior before deleting old owners.** Lost notifications,
   old queued payloads, retained UNKNOWN attempts, late receipts and poisoned-row
   recovery must work through actual worker entry points. Keep old aliases until
   persisted schedules, broker retries and rollback no longer require them. Move
   alerts with the code and verify their failure cases.

Rollback is part of the migration design, not just a package pin change. Once new
workers have written evidence, old workers must read that same authoritative
history before they can resume. Test the reverse path or define a forward repair
plan and an explicit irreversible boundary before deployment. The audit does not
authorize deleting evidence or re-running an uncertain external operation.

**Done means:** one authoritative evidence store, compatible in-flight messages,
correct application hooks, all 17 application suites plus product journeys passing
through Due, no lost harness assertions, retained native/migration compatibility,
and verified operational visibility. None of those gates is waived by the current
symbol inventory or standalone test count.

[be-models]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/durable_work/models.py
[be-receipts]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/durable_work/evidence/effect_evidence.py
[be-installed]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/installed.py
[be-wiring-state]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/tests/harness/wiring_state.py
[be-conftest]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/conftest.py
[be-reporting]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/tests/harness/due_work/work_table/reporting.py
[be-collection]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/tests/harness/session/durable_suites.py
[be-medialive-contract]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/streams/medialive/tests/test_medialive_creation_contract.py
[be-medialive-histories]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/streams/tests/harness/medialive_provisioning.py
[be-handoff]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/tests/harness/due_work/profiles/automatic_recovery/handoff_crashes.py
[be-medialive-races]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/streams/tests/harness/medialive_interleavings.py
[be-enrollment]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/durable_work/tests/conformance/test_conformance_enrollment.py
[be-wake-tests]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/durable_work/tests/capabilities/test_dependent_wake.py
[be-mirror-tests]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/durable_work/tests/declarations/test_mirror_receipts.py
[be-callback-tests]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/durable_work/tests/declarations/test_simple_callbacks.py
[be-rejection-tests]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/durable_work/tests/capabilities/test_operation_rejection_evidence.py
[be-scale]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/tests/harness/due_work/tests/work_table/test_query_scale.py
[be-wiring]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/wiring.py
[be-registry]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/shared/durable_work/runtime/registry.py
[be-alerts]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/observability/grafana/observability.yaml
[be-evidence-boundary]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/deployment/scripts/check_durable_evidence_boundary.py
[be-manifest]: https://github.com/gigaverse-app/gigaverse-backend/blob/04786fb2d0c0027526785c415178d0486d9dc989/gigaverse/core/durable_work_registry.py
