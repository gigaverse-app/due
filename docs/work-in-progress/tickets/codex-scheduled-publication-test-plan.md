# Scheduled publication

Source scenario: Temporal samples-python/updatable_timer at
ff0f1343822e532a5c377f02003002d2dcac4f79. Adapt the movable deadline;
add a real file publication, explicit cancellation outcomes and recovery evidence.
Reuse ScheduledAction, provider probes, existing storage and process helpers.

- [ ] Generated conformance: real admission/rollback, lost notification, retry/exhaustion, replay, stale snapshot and cancellation after provider entry (including worker death).
- [ ] Application histories: earlier/later deadlines; no publication before due; cancelled and obsolete deliveries do not publish; replacement after entry refuses; product time/status stay consistent.
- [ ] HTTP/CLI entry points: create, reschedule, cancel, inspect outcomes and validation.
- [ ] Real worker + Beat recovery: lost notification and worker restart still publish at the current deadline; cancelled work stays absent externally.
- [ ] Negative control: remove a production behavior and verify the real application proof fails, then restore it.
- [ ] CI: enroll the application suite and require its complete report; run full suite, lint/types, migrations and dependency matrix.

No Temporal runtime dependency or copied workflow code. Preserve links to the
original sample and inspected commit; describe adaptation rather than claim parity
with Temporal's workflow engine or a performance comparison.
