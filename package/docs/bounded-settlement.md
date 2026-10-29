# Experimental bounded completion commits

`DurableBulkExternalOperation.attempt_many(ids, settlement_batch_size=8)` is an
opt-in prototype for independent self-owned work rows. It uses the ordinary
engine for each claim, provider call, receipt and retry. The existing single-row
APIs remain synchronous and unchanged.

```python
# IDs come from the application's ordinary eligible-work selection.
outcomes = delivery.attempt_many(ids, settlement_batch_size=8)
```

Every successful provider receipt still commits before local product settlement.
At most 64 completions can share a transaction; the default is eight. The final
partial group flushes when iteration ends. The batch locks owned work rows in
primary-key order with one query, then uses each row's existing preparation,
`save()`, signals, completion hooks, timestamps and evidence finalization. This
shares commits and lock reads. It does not combine the per-row UPDATE statements.

| Scenario | Ordinary execution | Explicit `attempt_many` |
| --- | --- | --- |
| Product visibility | Each row commits before the next provider call | Successful rows commit at a batch boundary |
| Provider receipt | Separate durable commit | Same separate durable commit |
| Local hook failure | That row rolls back and enters ordinary failure handling | Group rolls back; ordinary completion is retried per row; healthy rows commit; errors are raised together |
| Failed local settlement | Normally marked UNKNOWN immediately | Remains CLAIMED with durable evidence until registered recovery |
| Local hooks | Per-row transaction | Must tolerate transactional replay and must not mutate another buffered row |
| Locks | One work row at a time | Up to the selected bound until all local hooks finish |
| Limit | One row | Number of successful completions, not elapsed time; a later provider call can delay visibility |
| Provider failure | Propagates to caller | Earlier receipts flush, then failure propagates; later IDs are not attempted |
| Process death | Existing recovery | Existing recovery consumes durable receipts; no in-memory buffer is needed |

Booleans retain `attempt_execution`'s transition meaning and include the final
fence check. They are not a substitute for the completion-status API. Partial,
retryable and ambiguous results follow their existing immediate paths. A lost
provider reply still requires definite evidence before any resend; this prototype
does not solve that ambiguity.

Use only self-owned rows without deadline notifications. All rows must use the
default database. Hooks must perform transaction-local changes or register safe
after-commit work. They must not rely on an earlier row's commit being visible to
another connection before the next row executes. Existing code that needs those
boundaries should continue calling `attempt_execution`.

This prototype is not a new worker, queue, work table or recovery loop. Its value
must be established against the native application path as well as ordinary Due.
Fewer commits alone do not establish lower CPU, database work or latency.
