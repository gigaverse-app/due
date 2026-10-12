# Upstream cutover improvements

Port the reusable coordination, binding and integration changes
from the consumer's tested library tree. Audit the pre-cutover implementation as
well as that tree so an export cannot silently omit an earlier improvement.

- [x] Private observation delivery histories remain covered; unused native
  lifecycle APIs are intentionally excluded from the upstream scope.
- [x] Full runtime and conformance suite: real PostgreSQL and Redis/Celery, with
  executed profile reports and existing debt kept visible.
- [x] Configured task, metric and table identities: standalone controls for
  preserving an application's deployment contracts.
- [x] Source audit: account for the remaining original definitions, test
  controls, documentation and tooling against their public owners.
- [x] Packaging: wheel imports, migration drift, Ruff and Pyrefly.
- [x] Consumer compatibility: exercise the application against both upstream
  candidates together, in addition to each standalone repository's CI.

## Validation (2026-10-12)

- Full locked suite against the public obligation candidate: **1,357 passed,
  four existing strict expected failures**, including real Celery workers.
- Recovered database/discovery/phase controls: **41 passed**; collection controls:
  **nine passed**; deployment-name controls: **two passed**.
- Obligation candidate: **936 core passed, one optional-dependency skip;
  364 Django passed, one existing expected failure**.
- Combined BE adopter/migration/policy/counter run: **2,113 passed, 70 existing
  expected failures, six existing collection exclusions**.
- Ruff and Pyrefly pass; migrations report no changes; the wheel imports every
  runtime module with no test dependencies and confirms the native package is
  absent. Wheel and source archive build.
- Public locked/minimum-version CI is required on the final revision. These
  local results do not claim production activation or repair existing debt.

See the [source/control audit](../../cutover-audit.md) for ownership and deliberate
exclusions.
