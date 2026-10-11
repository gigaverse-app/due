# Audit-driven application integration and guides

Builds on Due PR #9 and its canonical pytest-obligation dependency; do not duplicate
its handoff driver, generic assertions, mutation controls or query-scale proof.

- [x] Application fatal error in a real recovery phase aborts before the next identity, preserves owed rows and emits an aborted outcome; ordinary row failures still isolate and continue.
- [x] Wrapped fatal application errors and Due's existing stop signals retain their behavior; configuring hooks does not leak across tests.
- [x] Host context and receiver fault bindings exercise the canonical pytest-obligation integration, preserving default PostgreSQL/Celery capabilities.
- [x] Documentation examples and links match supported APIs; ASCII and Mermaid diagrams explain transaction, evidence and proof boundaries.
- [ ] Ruff, Pyrefly, full PostgreSQL/Redis suite, report gates, migration drift and wheel checks pass.

BE table transfer, task compatibility rollout and the 17 actual BE adopters remain
application migration work; this change makes no production cutover claim.

## Failure-first evidence

Before wiring the fatal-error classifier, six new cases failed: application
errors incorrectly continued recovery or were reported as isolated row failures.
Before passing host options through, three host cases failed at construction.
With the implementation, all 37 focused cases pass.

The full-suite run also exposed a pre-existing false positive expectation in the
primitive gate fixture. Its test-authored readiness transition is now explicitly
rejected by the canonical production-binding assertion; the three runtime gate
positive controls remain intact. All 79 enrollment controls pass.

Documentation checks parse all four Mermaid diagrams and six Python snippets,
and resolve local Markdown file targets. No backend adopter cutover is implied.
