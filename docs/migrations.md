# Migrating to Due

This guide evaluates migration from Python task queues and durable workflow
libraries. It is being developed against Due commit
`65e0bcfe426c9e663aec4ae5c71398965dde3269` and upstream documentation reviewed on
2026-10-11.

The comparison will distinguish source-compatible calls from preserved runtime
semantics, explain safe cutover of existing work, and identify which Due
capabilities require an explicit application change. Compatibility adapters are
proposals, not shipped integrations.
