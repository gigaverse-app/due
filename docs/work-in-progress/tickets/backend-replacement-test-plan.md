# Backend replacement parity

The previous extraction retained the runtime but omitted the mature work-table
suite generator. A replacement must preserve both application behavior and the
proofs required from each adopter.

- [ ] Inventory backend imports, registered modes and suite declarations against Due.
- [ ] Port effect_contract_suite and EffectExamples using pytest-obligation as the proof owner.
- [ ] Preserve missing-proof, collection, skip/xfail and enrollment guards.
- [ ] Exercise replay, external operations, observation, projection, scheduling and compensation.
- [ ] Include producer/rollback, synchronous caller, receipt repair, poison/stop,
  dependent wake, database index, discovery-edge and concurrency histories.
- [ ] Run mutation controls that reject broken binding or production behavior.
- [ ] Verify standalone PostgreSQL/Redis tests, typing, packaging and CI.
- [ ] Document exact remaining backend cutover work; keep native APIs private.
