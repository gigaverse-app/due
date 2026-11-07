# Python source contracts

- [x] Existing authority lookup and bounded batch tests pass before changes.
- [ ] Prove actual delay initialization and step bodies, including saturation.
- [ ] Prove the runtime claim fence and ordered callback admissions from source.
- [ ] Check normalization against integer/UUID keys, enum/string states and fused claims.
- [ ] Replay two/three-row PostgreSQL batches with stale state/token/revision,
      persistent callback failure, callback-entry counts, rollback and receipt evidence.
- [ ] Require proof target enrollment and all runtime cases; prove omission fails.
- [ ] Mutate current source; require semantic proof/test failures and a restored baseline.
- [ ] Build/install the wheel, route dependencies in CI and document the maintained command.
