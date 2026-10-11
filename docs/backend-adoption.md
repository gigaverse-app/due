# Backend replacement audit

The initial extraction retained the work-table runtime but only exposed a small
`DurableWorkBindings` contract. It omitted the backend's mature suite generator
and application-bound histories. That was not sufficient to replace the embedded
primitive without losing conformance coverage.

The port now includes `EffectExamples`, `effect_contract_suite`, producer and
inline-caller histories, receipt and observer histories, compensation and revision
scenarios, discovery admissibility, concurrency, lock order, migrated-index
checks, collection/enrollment guards, reporting and the background-work driver.
Shared semantic assertions remain in pytest-obligation. Due supplies adapters to
its actual runtime and policy. Native-table composition is not public.

## Audit follow-through: package gaps versus application migration

The detailed [migration audit (PR #6)](https://github.com/gigaverse-app/due/pull/6)
identified separate runtime, harness and application migration issues. The code
on this branch builds on the restored harness in PR #9; it does not imply those
changes are all released or that BE has cut over.

| Audited gap | Package support on this branch | Still owned by BE |
| --- | --- | --- |
| Handoff outcome and downstream recovery | Public `HandoffHistory.expected` / `recovery_ticks`; bounded delivery with ETA draining and leftover-message rejection | Bind real product outcomes and downstream ticks |
| Missing proof controls and query-scale evidence | Gate, wake, receipt, rejection, callback race and retained-history controls restored | Run the actual adopter declarations and custom query proofs |
| Application wiring error has a different class identity | `ApplicationHooks.abort_recovery_on` aborts recovery for configured application exceptions, including wrapped errors | Configure the application's actual error types in every process |
| Application context / hook fault visibility | `work_table_host(ambient_context=..., receiver_breaker=...)` composes canonical shared proofs | Supply actual context and relevant framework hooks; preserve fixture isolation |
| Different module, task, metric and command names | Runtime capabilities remain available under Due's namespace | Imports, old messages, schedules and operational consumers need adaptation |
| Existing evidence tables / receipt foreign keys | Fresh-install schema and canonical evidence runtime exist | Explicit populated/fresh migration rehearsal; no competing evidence stores |
| Pytest plugin/type ownership and 17 adopter suites | Public library contracts, collection guards and reporting exist | Resolve duplicate options, convert embedded contract types, run every adopter |

**Native lifecycle APIs are not a BE migration blocker.** All 17 registrations
use work tables; BE has no direct production adopter of `RecoveryLease`,
`CompletionLease` or `RecoveryRoute`. The two production imports from
`native.counters` use arithmetic only. Work-table observation's internal recovery
helper is already ported privately. Preserve those arithmetic imports and unrelated
application workflows without introducing a public native API.

Read the [integration guide](integration.md), [proof authoring guide](conformance.md)
and [executable recovery walkthrough](recovery-walkthrough.md) for the supported
package path. The 17 BE adopter suites and data migration remain unexecuted here.

## Read-only consumer inventory

Audited backend source: `04786fb2d` (the checkout used for extraction). Reproduce
against another checkout before attempting a cutover:

```bash
uv run python -m scripts.check_backend_compatibility /path/to/gigaverse-backend \
  --report /tmp/due-backend-imports.json
```

The audit resolves **305 consumer import statements to 120 distinct available
symbols**, including relative imports and the work-table harness. It separately
identifies **three native-counter imports** and **two application broker-fixture
imports**. Those are not work-table runtime API gaps. The script checks names,
not callback correctness, import-time application wiring or data migration safety.

The application manifest has **17 work-table registrations**. They cover
publication, rendition generation and cleanup, provider provisioning and teardown,
recording, scheduled cancellation, and export. The application-owned models,
provider adapters and their existing `EffectExamples` remain in the backend; they
must run against Due to prove a completed replacement.

| Existing ownership | Due mapping / required action |
| --- | --- |
| Embedded work-table runtime | `due.django` and its `public`, `capabilities`, `evidence`, `runtime`, `testing` modules |
| Pure errors and evidence values | `due.errors`, `due.observations`; these do not initialize Django |
| Work-table conformance package | `due.django.conformance` (authoring in `.suite`) |
| Background driver / thread connection helper | `.conformance.background`, `.conformance.worker_connection` |
| Application registration manifest | Remains application-owned; install into Due's registry at every process entry point |
| Registration test fixture | Provide `durable_work_registrations`; Due's fixture no longer imports the backend manifest implicitly |
| Redis worker fixture and wiring cleanup | Remain application-owned: they choose test Redis isolation and restore application wiring |
| Native counter users | Retain their backend-owned helper; do not expose native lifecycle APIs through Due |
| Frozen migration schema helper | Keep `durable_effect_schema_v1` and historical migrations intact |

## Work required before removing the embedded primitive

1. **Transfer infrastructure tables without losing evidence.** The backend owns
   `core_effectattempt` and `core_recoverydeferral`; Due's app owns different table
   names. Empty Due tables would sever prior uncertainty, receipts and deferrals.
   Design and test an explicit state/database migration retaining all identities,
   receipt foreign keys, constraints and indexes. Do not simply add Due's app and
   delete the old models. Rehearse both populated and fresh-database migration paths.
2. **Preserve application model identity.** Keep work-table app labels, primary
   keys, immutable admission times, revisions and provider idempotency keys. Those
   keys include model labels. Due's tenant field is a neutral 64-character field;
   retain the backend's 16-character choices through an application abstract base
   or an intentional, tested field migration and validation decision.
3. **Install tenant, read-after-write and fatal-error hooks.** Bind `ApplicationHooks` to the
   backend's tenant scope and replica-pin callback. Install them and the same
   registration manifest in request, worker, Beat and command entry points.
   Test actual primary/replica routing, not just the default database.
4. **Bridge queued task names and schedules.** Existing messages and persisted
   Beat entries contain the embedded task paths. Drain them or retain compatibility
   task aliases until they cannot arrive, preserving registration names, captured
   tokens, queues and time limits. Avoid running duplicate old/new Beat schedules.
5. **Move the harness with its adopters.** Rewrite work-table suite imports; retain
   the exact production capability objects and domain examples. Configure the Due
   host and application fixture wiring. Run all 17 adopter suites, their enrollment
   inventory and product journey tests. Do not replace them with the library's
   example suite. Keep native contracts in their current owner.
6. **Update operations.** Move metrics/alerts from the embedded metric prefix to
   `due_`, preserve worker routing and read-only operator inspection, and validate
   startup and recovery against the migrated database and existing receipts.

No backend production cutover, table transfer, task-name bridge or deployment has
been performed by this PR. The standalone evidence establishes the extracted
library and its harness, not completion of these application migration steps.

The final harness adapters also expose `conformance.background_beat` (`run_beat`,
`tick`, `beat_entries_for`) and `conformance.interleavings` (`BrokerControl`,
`environment`, `connection_scope`). These retain the canonical background driver
and worker connection scope; they do not create another broker simulation.
Beat imports tasks once per Celery app, reads that app's schedule, executes
selected real task bodies, and drains their published work. Interleaving controls
capture/drop/release notifications and redeliver the exact captured message.

The final port also preserves `HandoffHistory.expected` and `recovery_ticks`
through pytest-obligation. `WorkTableDelivery` drives three bounded recovery
rounds, including downstream ticks and delayed messages, and refuses leftover
messages whose outcomes were not observed. It temporarily lifts application
instance-level task recorders and restores them when the history ends.

The conformance self-tests now include BE's generated-gate, dependent-wake,
receipt-repair, rejection-policy and simple-callback race controls. The shared
retained-history test checks actual PostgreSQL row visits and buffers, with
renamed-index positive controls and missing-index/full-history-read mutants.
Observation tests exercise closed execution gates, real Redis/prefork delivery,
lost and stale observation tokens, and policy-window evidence retention.

These close harness gaps; they do not establish a BE runtime cutover. Plugin
ownership, persisted evidence/model identity, old task paths, application hooks,
metrics and all 17 application bindings require a separate BE migration.
