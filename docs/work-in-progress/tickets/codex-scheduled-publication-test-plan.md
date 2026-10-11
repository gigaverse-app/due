# Scheduled publication

Source scenario: Temporal samples-python/updatable_timer at
ff0f1343822e532a5c377f02003002d2dcac4f79. Adapt the movable deadline;
add a real file publication, explicit cancellation outcomes and recovery evidence.
Reuse ScheduledAction, provider probes, existing storage and process helpers.

- [x] Generated conformance: real admission/rollback, lost notification, retry/exhaustion, replay, stale snapshot and cancellation after provider entry (including worker death).
- [x] Application histories: earlier/later deadlines; no publication before due; cancelled and obsolete deliveries do not publish; replacement after entry refuses; product time/status stay consistent.
- [x] HTTP/CLI entry points: create, reschedule, cancel, inspect outcomes and validation.
- [x] Real worker + Beat recovery: lost notification and worker restart still publish at the current deadline; cancelled work stays absent externally.
- [x] Negative control: remove a production behavior and verify the real application proof fails, then restore it.
- [x] CI: enroll the application suite and require its complete report; run full suite, lint/types and migrations; require both CI dependency lanes.

No Temporal runtime dependency or copied workflow code. Preserve links to the
original sample and inspected commit; describe adaptation rather than claim parity
with Temporal's workflow engine or a performance comparison.

Validation: 64 generated scheduled-publication conformance cases, 7 application
journeys and 1 real worker/Beat restart journey pass. The complete local suite
passes (1,355 passed; 4 existing xfails). Ruff, Pyrefly, migration application and
drift check, and `due_check` pass. The scheduled A/B/E/H/I/J and effect-report gate
passes against the full-suite artifacts.

Negative control: replacing `PublishAtDeadline.perform_effect` with a no-op in a
process-local patch made the lost-wakeup journey fail on missing published file
bytes, despite the product reporting published. Removing the patch restores the
passing test. CI runs the same application in locked and minimum-Django lanes;
its final run status is linked from PR #18.

## Transient deadline hints and submission identity

- [ ] Root scheduled mechanism: near deadlines execute without Beat; far deadlines enter a bounded lookahead; early/stale/duplicate hints cannot execute old work or skip backoff. Lost hints retain ordinary recovery.
- [ ] Cost contract: publishing and inspecting future hints performs no SQL writes; one bounded page per recovery tick, no hint ledger. Broker failures cannot prevent ordinary due dispatch.
- [ ] Submission identity: one product/work row across retries and concurrent callers; changed input conflicts; replay after reschedule/cancel/completion returns current state. Rollback leaves neither row. Browser and CLI retain caller keys.
- [ ] Existing-row migration, generated conformance, real worker restart, full CI and live comparison with unchanged five-second recovery cadence.
- [ ] Report latency plus SQL writes/row and message counts, retaining raw evidence outside mergeable history.
