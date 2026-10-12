# Application correctness and immutable provider replay

Compare the pinned Procrastinate book demo and Temporal timer demo with the corresponding Due applications; distinguish added application behavior from engine capabilities.

1. Require an independent expected outcome for application process histories through pytest-obligation. Bind books, documents and scheduled publication. Negative controls must reject a wrong product/provider outcome even when normal and recovered runs agree.
2. File revisions must be absent or complete, never truncated during replay. Equal retries must avoid rewriting provider bytes; conflicting use of an operation key must fail without replacing existing content. Reproduce before changing production code; cover actual process death, conflicting concurrent writers and retries through the scheduled command/worker/recovery.
3. Run matched sequential before/after provider measurements for fresh writes and replays, count bytes written and state the tradeoff. Keep raw evidence outside mergeable history. No SQL ledger or extra durable work rows.
4. Run generated conformance and full suite on PostgreSQL/Redis, lint/types, and CI. Preserve existing debt explicitly and gate the new scheduled process proof in CI.

## Follow-up: cheaper first publication without weaker guarantees

Compare the reviewed directory-staging provider with exclusive-file staging under the same atomic hard-link install. Preserve bytes, conflict behavior, normal file permissions, process-death visibility and zero-write replay. Use a provider-boundary partial-write fault (shared by both implementations), generated application process proofs and a generated independent replay outcome. Measure alternating fresh/replay batches and filesystem operation counts, outside mergeable source history.
