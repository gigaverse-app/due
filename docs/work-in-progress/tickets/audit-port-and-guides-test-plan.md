# Audit-driven application integration and guides

Builds on Due PR #9 and its canonical pytest-obligation dependency; do not duplicate
its handoff driver, generic assertions, mutation controls or query-scale proof.

- [ ] Application fatal error in a real recovery phase aborts before the next identity, preserves owed rows and emits an aborted outcome; ordinary row failures still isolate and continue.
- [ ] Wrapped fatal application errors and Due's existing stop signals retain their behavior; configuring hooks does not leak across tests.
- [ ] Host context and receiver fault bindings exercise the canonical pytest-obligation integration, preserving default PostgreSQL/Celery capabilities.
- [ ] Documentation examples and links match supported APIs; ASCII and Mermaid diagrams explain transaction, evidence and proof boundaries.
- [ ] Ruff, Pyrefly, full PostgreSQL/Redis suite, report gates, migration drift and wheel checks pass.

BE table transfer, task compatibility rollout and the 17 actual BE adopters remain
application migration work; this change makes no production cutover claim.
