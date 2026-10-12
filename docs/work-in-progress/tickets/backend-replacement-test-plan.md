# Backend replacement parity

The previous extraction retained the runtime but omitted the mature work-table
suite generator. A replacement must preserve both application behavior and the
proofs required from each adopter.

- [x] Inventory backend imports, registered modes and suite declarations against Due.
- [x] Port effect_contract_suite and EffectExamples using pytest-obligation as the proof owner.
- [x] Preserve missing-proof, collection, skip/xfail and enrollment guards.
- [x] Exercise replay, external operations, observation, projection, scheduling and compensation.
- [x] Include producer/rollback, synchronous caller, receipt repair, poison/stop,
  dependent wake, database index, discovery-edge and concurrency histories.
- [x] Run mutation controls that reject broken binding or production behavior.
- [x] Verify standalone PostgreSQL/Redis tests, typing, packaging and CI.
- [x] Document exact remaining backend cutover work; keep native APIs private.

Local completion: 821 tests pass; Ruff, Pyrefly, migrations, profile/application
report gates and isolated installed-wheel smoke pass. CI is tracked on Due PR #5.
See [the cutover audit](../../../package/docs/backend-adoption.md) for remaining BE integration
work; the application itself has not been migrated.
