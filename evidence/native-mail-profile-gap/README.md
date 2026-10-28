# Due attempt-read costs against native mail

Two independent improvements in Due #165 and #166, measured separately and together. Both apply automatically to scalar and bulk work. Both PRs now include Due #167, which removed claim/entry composition. The final comparison uses #5549 with that option entirely absent; the earlier runs with the option are retained separately. API, evidence durability, lock ordering and transaction boundaries are preserved.

The supported result is small: #165 reduces CPU for an individual locked attempt read, and #166 removes one SQL call per provider entry. **These runs do not establish a reproducible end-to-end mail latency win, and Due still trails native mail.**

## Final comparison after the claim/entry revert

Both PRs contain main at be31f83 (#167). #165 is acc0299; #166 is 74ebd36. The latest application baseline is Backend #5549 at 94f6f416. Candidate branches change only the vendored Due runtime. Each arm has 100 measured campaigns across forward/reverse 50-sample blocks with the same real shortlink behavior and scripted SES success responses.

| Delivery metric, lower is better | Frozen native | #5549 baseline | #165 | #166 | Both |
| --- | ---: | ---: | ---: | ---: | ---: |
| SQL statements | 40 | 59 | 59 | 57 | 57 |
| Writes | 13 | 17 | 17 | 17 | 17 |
| Explicit commits | 7 | 9 | 9 | 9 | 9 |
| Median wall time, ms | 29.98 | 49.18 | 47.09 | 46.44 | 47.32 |

Admission adds 12 statements, 4 writes and 1 commit in every arm. Total SQL is 52 native, 71 baseline, 69 with #166 or both. The combined result still has 17 more SQL statements, four more writes and two more commits than native. This run's modest positive latency difference does not overcome the uncertainty from the other runs below. No end-to-end latency or database CPU reduction is claimed.

The frozen native commit remains 1ea8f661. The checked mail and shortlink paths have no diff from that snapshot to backend main bb0efaec. This is a fixed source comparison, not a claim of measured production speed.

Final exact-head CI is green: [#165 acc0299](https://github.com/gigaverse-app/due/actions/runs/40788915513) runs 2,166 library tests, and [#166 74ebd36](https://github.com/gigaverse-app/due/actions/runs/40788918749) runs 2,164. Both Django variants also pass 1,076 integration checks plus two expected failures, and 286 Acme tutorial checks. Standalone export passes too. The locked variant's full obligation reports are in retained-ci-obligation-reports.zip. CI-esta sticky comments [#165](https://github.com/gigaverse-app/due/pull/165#issuecomment-6483236208) and [#166](https://github.com/gigaverse-app/due/pull/166#issuecomment-6483227953) identify these exact heads and completed runs. Widget performance was not admitted by ordinary affected policy; the skipped comparison is not claimed as executed evidence.

Final source proofs: each isolated application contract has **127 passed**, one live-worker case deselected. The combined engine has **897 passed**, four live-worker cases deselected and three expected failures. A/B/C/E/G/J remain verified for each arm. The refreshed entry-budget red/green test uses main after #167 and the current candidate. The direct lookup microbenchmark was rerun on acc0299: mean wall **0.4044 -> 0.2863 ms/read** (29.2% lower), Python CPU **0.3109 -> 0.1984 ms/read** (36.2% lower), with the same alternating 10 x 1,000-read method. This is still a lookup-only measurement.

`retained_engine.jsonl.gz` holds the new rows and `patches/*-after-revert.patch` the current library changes. The mail application deltas are against accessible #5549 commit 94f6f416. Run `run_gap_retained.py` and `run_gap_retained_proofs.py` in the sibling layout described below, using mail-gap-retained-base/lookup/guard/both. Earlier artifacts are historical measurements and do not supply the current PRs' CI status.

## Earlier measurements with the now-reverted claim/entry option

The sections below preserve the original run. Due #167 subsequently reverted the claim/entry option and Backend #5622/#5623 were closed. The retained-engine comparison above supersedes these counts. Historical results must not be presented as current #5549 counts.

### Deterministic persistence counts

Real PostgreSQL 15.19, 51 recipients in two chunks, real shortlink behavior, scripted successful SES responses. Counts exclude fixture creation and verification. SQL statements include SET and savepoint statements; commits are counted separately.

| Delivery metric, lower is better | Frozen native | Due baseline | #165 | #166 | Both |
| --- | ---: | ---: | ---: | ---: | ---: |
| SQL statements | 40 | 55 | 55 | 53 | 53 |
| INSERT/UPDATE/DELETE statements | 13 | 15 | 15 | 15 | 15 |
| Explicit commits | 7 | 7 | 7 | 7 | 7 |

Admission is identical: 12 statements, 4 writes, 1 explicit commit. Total SQL is therefore 52 native, 67 Due baseline, 65 with #166 or both. No additional shortlink batching or app-specific shortcut was introduced. Every sample verifies 51 accepted recipients, one acceptance per recipient, completed campaign and sent event.

## Focused locked-read measurement

`gap-lookup-micro-final.json` measures exact #165 code at 7d50543. Ten alternating ORM/fixed-reader blocks, 1,000 reads per arm per block, 20 warmups per arm, one real JSON-bearing attempt and an open transaction. No commit is timed in this microbenchmark.

| Mean per read, lower is better | ORM | Fixed reader | Reduction |
| --- | ---: | ---: | ---: |
| Wall time, ms | 0.3550 | 0.2473 | 30.3% |
| Python-process CPU, ms | 0.2828 | 0.1719 | 39.2% |

This is lookup cost, not campaign speed or PostgreSQL CPU. Four of these reads occur per two-chunk Due delivery. The earlier micro run is retained too. Windows process CPU has coarse resolution, so aggregate blocks are used.

## Mail timing uncertainty, including unfavorable runs

Median delivery milliseconds, excluding one warmup per block (or per variant for paired runs):

| Run | Samples per arm | Native | Due baseline | #165 | #166 | Both |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| First clean separate sources | 100 | 30.28 | 47.89 | 55.37 | 60.09 | 53.53 |
| Known-column separate sources | 60 | 30.04 | 45.03 | 43.05 | 43.23 | 44.56 |
| First paired diagnostic | 60 | 31.09 | 43.33 | 42.66 | 43.19 | 42.89 |
| Final paired diagnostic | 100 | 42.41 | 57.92 | 59.35 | 59.72 | 61.49 |

Identical-code A/A controls also move: baseline versus baseline_control 81.86/83.04 ms, combined versus combined_control 84.52/81.72 ms (100 each). Variance is large enough to change the apparent direction of these small wins. No latency acceptance target is met by these results. Raw favorable and unfavorable samples, process CPU, SQL timings and unparameterized statement traces are retained in the gzip files and summarized in measurements.json.

Separate-source runs use A/B/C/D/E/E/D/C/B/A order on committed sources. The known-column run predates a typing-only `cast(str, field.column)` addition. Final paired runs use exact final library code but restore the old helpers with scoped patches for control arms, so they are diagnostics, not an independent frozen-source baseline. Paired native uses the adoption tree's native path, which has one additional selection read (41 delivery statements); the separate-source native has 40. Preliminary runs include a dirty prototype and are labeled accordingly in their provenance. No local DB suites ran concurrently with the final measurements; this does not prove an otherwise idle or controlled workstation.

## Profile and next bottlenecks

Separate cProfile captures show added work in database waits and Django query construction/materialization. These profiles perturb execution and must not be used as unprofiled latency evidence. Bulk reducer/model work is much smaller. The mail adapter also repeats wide campaign/event/community reads and coordinates campaign slots. Eliminating those reads requires proving when fresh data is required; it is not part of these library-only changes.

The combined candidate still has 13 more SQL statements and two more writes per campaign than frozen native, with the same number of commits. Neither PR measures PostgreSQL CPU, WAL, production throughput, delivery-provider latency, or high-contention scalability. Those broader performance claims remain open.

## Correctness and obligation evidence

- #165 exact code CI: https://github.com/gigaverse-app/due/actions/runs/40786979713 (7d50543). 2,492 library tests, 1,076 integration tests plus two expected failures, and 286 Acme tests per dependency variant; live worker, Beat and process-recovery coverage runs in CI.
- #166 exact code CI: https://github.com/gigaverse-app/due/actions/runs/40785353305 (49980fe). 2,490 library tests; same integration and Acme coverage.
- Final local backend application contract: 128 passed, 1 live-worker case deselected for each separate optimization. Combined engine suite: 898 passed, 4 live-worker cases deselected, 3 expected failures. Local checks use real PostgreSQL and a scripted provider; they are not a backend live-worker deployment check.
- Mail obligation profiles A/B/C/E/G/J are claimed and verified in every arm. I/D/F are not applicable; H is explicitly declined because provider replay can duplicate mail. An overall report `verified: false` must not be presented as every profile failing or every guarantee passing. Exact per-profile assessment is retained in mail-profile-summary.json and the full reports.
- Existing entry controls exercise composition off/on and receipt layouts. Tests retain unknown-outcome blocking, stale-owner rejection, concurrent evidence writers and receipt recovery. No second engine or relaxed safety mode is introduced.
- Red-team regression: the #165 prototype used SELECT *, which failed when a rolling schema added a column colliding with a model property. It now selects only concrete fields known to that worker. The failing reproducer and passing focused log are included. Prior obligation suites exercise effect histories, not arbitrary schema evolution, so a direct real-database schema test was needed.
- #166's query-budget reproducer fails on main (3 SQL versus expected 2). Custom has_possible_effect overrides retain the original call path; the same built-in predicate is compiled for the combined clock/existence query.

CI-esta reports for these code heads match observed execution: all six planned lanes selected, 11 CI jobs executed, zero reused. Widget comparison is expensive work and was not admitted by ordinary affected policy; its green gate is not a widget performance measurement. Required code tests and report validators did execute. No CI-esta or pytest-obligation defect was found in these results.

## Reproduction and provenance

manifest.json records code hashes, CI runs and artifact hashes. Frozen native is backend 1ea8f661; Due baseline is backend #5623 at 3d50c515. These are frozen comparisons, not a claim about whatever main contains later. Final candidate application commits are local measurement checkpoints, not published PR refs. patches/ contains each library delta against the accessible baseline so those states can be reconstructed. Each JSONL row records its actual source and harness hash.

The scripts directory preserves the actual harnesses/drivers. They assume sibling checkouts named mail-flatten-main, mail-claim-entry, mail-gap-profile, mail-gap-guard and mail-gap-combined, a backend virtualenv, disposable PostgreSQL at localhost:55449, and the dependency versions of the frozen backend checkout. run_backend_arm.py loads only test_env.dummy and creates an ephemeral dummy RSA key in process. Adjust local paths/service ports when reproducing. Never target a production database.

Run the separate-source series with `python run_gap_final_v2.py` from that sibling layout; run proofs with `python run_gap_proofs.py`. For the focused reader, run `python -m pytest -c package/pyproject.toml <test_lookup_cost.py> --reuse-db` in the #165 checkout with LOOKUP_COST_OUTPUT and DUE_DATABASE_URL set to disposable paths. For paired/AA controls run the supplied pytest files through run_backend_arm.py, MAIL_BENCH_ENGINE=due, MAIL_BENCH_SAMPLES=100, MAIL_BENCH_BLOCK and MAIL_BENCH_OUTPUT set. Files append results, so choose a fresh output path per run.

This directory lives only on the closed, unmerged evidence PR #161 branch. It is evidence, not shipping runtime code.
