# Binding ergonomics and integrity

Improve both runtime integration and conformance provider bindings. Baseline:
`0a19778` (Due) and the backend's checked-out registry/conformance integration.
Keep existing lower-level APIs and application-owned outcome assertions.

- [x] One setup call configures task imports and schedules in Django and an explicitly supplied Celery app; preserve unrelated settings, reject schedule collisions before installing anything, remain idempotent.
- [x] Typed provider probes delegate real calls, count per identity, inject rejection before the call or response loss only after the real call completes, and restore patches/fault scopes after failures.
- [x] Migrate search and printer examples to the shared probe; retain physical provider observations and all generated guarantees.
- [x] Positive and broken-provider controls demonstrate that the new bindings cannot manufacture successful external effects.
- [x] Run generated conformance, real worker journeys, full tests, report gates, lint/type checks and CI; record actual outcomes in PR.

No backend cutover, schema change, performance claim or automatic guarantee inference.

## Evidence

After merging main at `5e2aba9`, the full local suite passed **1,237 tests**
with two intentional legacy-Celery negative controls xfailed. All six report gates
passed. Locked/minimum-Django CI, lint, type checks and wheel build also passed
at `4b04404`. One existing multithreaded-fork warning remained in the observation
smoke test. No new type-check suppressions were added.

A temporary no-op search provider caused the generated lost-notification proof
to fail on its independent product outcome. The real provider passes that same
proof. Twenty-two focused tests cover setup and provider probe boundaries.
The search/printer bindings replace five hand-written provider wrappers across
four external methods; application observations and guarantee requirements remain.

CI found a pre-existing periodic-enqueue race in the Procrastinate comparison.
An already-queued recovery regression failed before the driver fix; all eleven
comparison cases pass afterward. The driver consumes the existing singleton job
instead of requiring a duplicate enqueue. Final PR-head checks are recorded on
[PR #13](https://github.com/gigaverse-app/due/pull/13).
