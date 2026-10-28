# Static eligibility and captured membership: PostgreSQL measurements

## Method

Measured on 2026-10-28 with PostgreSQL 15.19 on Windows, real transactions and
fake SES. Backend source was `bf533c35b6871d720e32530c1f31d91e7e3246a5` (#5549,
including main `1ea8f661c`). All four arms use identical fixtures, shortlink
behavior, provider replies and the frozen mail persistence harness from
Backend `13ea57040:perf/scripts/measure_bulk_mail_persistence.py`.
Harness SHA256: `d4042ada33f11e164c86699bbe7b4fcb8fbbffa9d466ede9af6b712e01bb4d72`.

Due baseline: `5e329997a270418880fc88746bcf970d54075453`. Candidate runtime:
`68b3560` (later changes only add tests, docs and repair a conformance fault).
For the static-only arm, replace only `declarations/eligibility.py` and
`runtime/coordination.py`; for membership-only, replace only
`capabilities/operations.py` and `capabilities/bulk.py`, under `due/django/`.
The static and combined arms also replace mail's time-independent
`ExecutionEligibility(condition=lambda now: ~Q(campaign__status=CampaignStatus.PARKED))`
with the same Q directly. There are no other application changes.

Serial order: baseline, static, both, membership, membership, both, static,
baseline. Each block runs five scenarios, with one warmup plus five measured
campaigns per scenario: 240 total executions, 200 measured, ten per arm/scenario.
The harness measures admission plus delivery and checks recipient/provider
outcomes outside timing. SELECT/INSERT/UPDATE/DELETE/SAVEPOINT commands count as
SQL commands; explicit commits are measured separately. DML counts are commands,
not physical row writes, WAL bytes or index work.

## Command counts (lower is better)

Counts are identical across all ten repetitions in each cell.

| Scenario | Baseline SQL | Static only | Membership only | Both | Both vs baseline | DML / commits, all arms |
|---|---:|---:|---:|---:|---:|---:|
| 51 recipients, success, 2 chunks | 77 | 75 | 75 | 73 | -5.2% | 23 / 10 |
| 101 recipients, success, 3 chunks | 100 | 97 | 97 | 94 | -6.0% | 31 / 14 |
| 201 recipients, success, 5 chunks | 146 | 141 | 141 | 136 | -6.8% | 47 / 22 |
| 51 recipients, throttle, 3 provider rounds | 96 | 94 | 93 | 91 | -5.2% | 30 / 13 |
| 51 recipients, permanent subset | 77 | 75 | 75 | 73 | -5.2% | 23 / 10 |

Static eligibility saves one clock SELECT per claim. Membership reuse saves one
SELECT per provider round, including an inline retry. Neither change removes
receipt durability, locks, the fresh post-lock provider-entry clock, writes or
commits. No new cache, work table, migration or dependency is introduced.

## Elapsed time (local median milliseconds; lower is better)

| Scenario | Baseline | Static only | Membership only | Both |
|---|---:|---:|---:|---:|
| 51 success | 66.13 | 66.20 | 60.14 | 60.94 |
| 101 success | 92.26 | 89.05 | 83.39 | 86.10 |
| 201 success | 158.47 | 140.57 | 132.80 | 164.02 |
| 51 throttle | 84.18 | 77.61 | 73.77 | 81.40 |
| 51 permanent | 65.30 | 58.62 | 57.74 | 77.00 |

These local timings do **not** establish a consistent latency improvement. The
combined arm is slower in two scenarios despite removing the expected SELECTs.
Do not infer production throughput, p95, CPU savings or universal latency gains.
A controlled network-latency/load experiment is needed for those claims. The
proven benefit is fewer database commands with the same durable boundaries.

## Remaining gap to native main

The preceding serial ABBA measurement of the same frozen harness on native main
`1ea8f661c` measured SQL / DML / commits of 52/17/8, 63/22/11, 85/32/17,
61/22/10 and 52/17/8 for the five scenarios above. These are historical command
counts, not a fifth latency arm in this run. Both arms already had batched,
persisted shortlinks. Combined Due still costs 73/23/10 for two successful
chunks, compared with native's 52/17/8. This PR narrows the gap; it does not prove
parity with native mail.

## Reproduction and source identity

Use the frozen harness explicitly against disposable PostgreSQL databases.
Set `MAIL_BENCH_ENGINE=due`, `MAIL_BENCH_SAMPLES=5`, a distinct
`MAIL_BENCH_BLOCK`, and `MAIL_BENCH_OUTPUT`. Run `pytest` on the harness with
Backend's ordinary test environment and `--reuse-db`. Apply the exact file
combinations above and the Q declaration only to static/combined arms.

Temporary benchmark-only commits preserve the measured Backend trees:

| Block | Arm | Commit |
|---|---|---|
| 0 | baseline | a8d4cb167d6be3130795897c4158886b13530497 |
| 1 | static | e5686893a038c1ec3313278f07907a7d56d40a61 |
| 2 | both | 4e4975a451db06ebe037123e25dd2e3e21b5d654 |
| 3 | membership | f4da4aec2f25ef6ec33c40eb9225c9ee18aa8427 |
| 4 | membership | e14424f10196ab4d8a9857e6e26042a31f65e599 |
| 5 | both | e4888c6349fac167a6958b2f65e5e051c7a1d846 |
| 6 | static | d38a12657ea39a0230602ecc480e5fdfb61ec20f |
| 7 | baseline | 26eefe59a571e83918fe5c144c0860834e913ebb |

The local `static-capture-benchmark-manifest.json` retains per-file SHA256
digests. Raw traces are `mail-cost-static-capture-<block>-<arm>.jsonl` beside
the isolated worktrees. These local artifacts and benchmark commits are not
published dependencies; public reproduction uses the exact source combinations
above. No mail adoption branch was changed by this experiment.
