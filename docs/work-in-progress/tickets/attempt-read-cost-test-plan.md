# Attempt read cost

Preserve current evidence, row-lock serialization, database routing, JSON/UUID/time conversion, and all claim/receipt recovery semantics while reducing fixed lookup compilation.

The focused controls in `package/tests/evidence/test_locked_read.py` cover known-field round trips, fresh rows, write routing, missing identities, transaction enforcement and a newer schema during a rolling deployment. Existing bulk and generated obligation suites cover receipt recovery and overlapping writers. The optimization uses the ordinary engine after the claim/entry option was reverted.

[PR #165](https://github.com/gigaverse-app/due/pull/165) records executed CI, separate and combined native-mail measurements and raw evidence. A focused lookup CPU improvement does not establish a campaign latency improvement.
