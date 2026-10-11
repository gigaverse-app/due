# Backend migration audit

Audit in progress, 2026-10-11. Baselines: backend `04786fb2d`, Due
`65e0bcf` (merged PR #5). The current verdict is **not ready for a backend
cutover**: symbol parity is established, but schema ownership, pytest plugin
coexistence, application wiring and adopter execution need separate evidence.
This audit will record concrete blockers and acceptance gates below.


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
3. **Install tenant and read-after-write hooks.** Bind `ApplicationHooks` to the
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
