# Composed claim and provider entry

Keep the existing claim, provider evidence, settlement and recovery engine. An
explicitly compatible external capability may commit its claim and first
attempt together. The default path and stored formats remain unchanged.

- [x] Execute scalar/bulk acquisition through production capability entry points;
  compare opt-in and default commits, provider calls and product state.
- [x] Observe the committed UNKNOWN attempt from an independent PostgreSQL
  connection at provider entry, and acquire the work lock there without waiting.
- [x] Exercise direct and queued deliveries; duplicate delivery cannot resend.
- [x] Fail the started hook with a Python exception and an actual SQL failure;
  no attempt survives, retry accounting persists, and recovery delivers once.
- [x] Refuse the entry commit; no provider call occurs and the ordinary retry
  path accounts for the failed preparation.
- [x] Pause before entry; retain intent without an attempt or a charged retry.
- [x] Race a failed preparation with a competing executor; failure settlement
  must not consume the successor's ownership or duplicate its effect.
- [x] Run the existing generated pytest-obligation histories against distinct
  opted-in capabilities for inline and separate receipt storage. Mutation
  controls must reach the provider and detect a deliberately repeated call.
- [ ] Run existing default-path library and worker integration suites.
- [x] Audit Backend mail pre-entry hooks and run unchanged product goldens.
- [ ] Benchmark fixed native, current Due and composed-entry Due in alternating
  blocks with identical shortlinks and provider responses. Report SQL, writes,
  commits and elapsed time separately; retain raw evidence outside merge history.

Opt-in compatibility review: pre-entry hooks become short local transactional
preparation. They must not perform external I/O, wait for another connection to
observe the claim, or rewrite the work's authority, inputs or member progress.
The initial lease includes preparation. A preparation failure rolls back before
ordinary reacquisition and retry classification; a concurrent winner retains
its authority. Provider I/O, receipt durability and subsequent rounds retain the
existing execution boundaries.
