# Upstream cutover improvements

Port the reusable native lifecycle, coordination, binding and integration changes
from the consumer's tested library tree. Audit the pre-cutover implementation as
well as that tree so an export cannot silently omit an earlier improvement.

- [ ] Native recovery/completion histories: real PostgreSQL claims, expiry,
  successor fencing, cancellation, finalizer rollback and bounded dispatch.
- [ ] Full runtime and conformance suite: real PostgreSQL and Redis/Celery, with
  executed profile reports and existing debt kept visible.
- [ ] Configured task, metric and table identities: standalone controls for
  preserving an application's deployment contracts.
- [ ] Source audit: account for the remaining original definitions, test
  controls, documentation and tooling against their public owners.
- [ ] Packaging: wheel imports, migration drift, Ruff and Pyrefly.
- [ ] Consumer compatibility: exercise the application against both upstream
  candidates together, in addition to each standalone repository's CI.
