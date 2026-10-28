# Attempt read cost

Preserve current evidence, row-lock serialization, database routing, JSON/UUID/time conversion, and all claim/receipt recovery semantics while reducing fixed lookup compilation.

- [x] Real PostgreSQL receipt/diagnostics writer overlap and claim/entry boundary controls.
- [ ] Fixed lookup: primary routing, complete field round trip, no cached row, missing identity, transaction enforcement.
- [ ] Existing generated obligation suites with composition off/on and inline/separate receipts; mutation sensitivity remains required.
- [ ] Full library and integration CI; frozen native mail A/B/C/C/B/A, profile separately from latency.
