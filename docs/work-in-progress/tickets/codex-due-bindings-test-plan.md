# Binding ergonomics and integrity

Improve both runtime integration and conformance provider bindings. Baseline:
`0a19778` (Due) and the backend's checked-out registry/conformance integration.
Keep existing lower-level APIs and application-owned outcome assertions.

- [ ] One setup call configures task imports and schedules in Django and an explicitly supplied Celery app; preserve unrelated settings, reject schedule collisions before installing anything, remain idempotent.
- [ ] Typed provider probes delegate real calls, count per identity, inject rejection before the call or response loss only after the real call completes, and restore patches/fault scopes after failures.
- [ ] Migrate search and printer examples to the shared probe; retain physical provider observations and all generated guarantees.
- [ ] Positive and broken-provider controls demonstrate that the new bindings cannot manufacture successful external effects.
- [ ] Run generated conformance, real worker journeys, full tests, report gates, lint/type checks and CI; record actual outcomes in PR.

No backend cutover, schema change, performance claim or automatic guarantee inference.
