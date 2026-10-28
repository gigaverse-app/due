# Entry evidence read cost

Preserve the locked-row lease, unresolved-effect rejection, custom evidence methods, and controlled-clock behavior. Share the existing scalar/bulk query with the fresh database-clock read inside the ordinary attempt transaction.

The focused controls in `package/tests/evidence/test_entry_check.py` check two SELECTs (work lock and combined clock/evidence) plus one UNKNOWN insert, expired-lease refusal, custom policy overrides and duplicate-crossing rejection. Existing scalar/bulk generated obligation suites cover evidence history and both receipt layouts. Claim/entry composition is not required and has been removed upstream.

[PR #166](https://github.com/gigaverse-app/due/pull/166) records executed CI and counterbalanced native-mail measurements, independently and with #165. Fewer queries do not establish a latency, database CPU or throughput improvement.
