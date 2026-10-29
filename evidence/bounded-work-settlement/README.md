# Bounded work settlement experiment, Due #168

Recommendation: keep [Due #168](https://github.com/gigaverse-app/due/pull/168) as a
draft experiment. Do not adopt it into mail on this evidence. It removes lock
queries and commits, but does not demonstrate a consistent CPU, database-work
or latency improvement. Native remains substantially cheaper.

## Sources

- Native backend: `1ea8f661c2ae393f0dc3b1a43603d17af685c0ff`.
- Ordinary Due mail: `fd48b442460014179815fd953d14642f7ec93d93` (backend #5549 plus merged Due #165/#166).
- Prototype mail: `a9ca7d95fd8f702c001f23f816839644a04f10ad`. Its four changed Due modules are identical
  to the public prototype runtime at `4873f97ed22f8f536b2ab8faa425edf284c06fe3`.
- Reviewed Due head: `f0e1122325224ecd77f95745ad7dabd57748da04`; subsequent changes are tests/documentation.
- Backend main checked: `804a23ffdd21121504e4ceff17d0f5e93f53d593`. Mail and shortlink
  paths have no changes from the pinned native arm.
- The mail patch here is a **benchmark-only success-path adapter**, not a proposed
  production adoption. No shortlink batching or other unrelated optimization.

## Method and limitations

Real PostgreSQL 15.19 on Windows, dedicated disposable cluster, fsync and synchronous
commit enabled. `pg_stat_statements` includes planning and execution statistics.
Three real application campaign sizes, with identical fake SES acceptances and
unchanged shortlink behavior. Admission, fixture creation, validation and output
are outside delivery timing. One discarded warmup per case per block. Sequence:
native, ordinary, bounded, bounded, ordinary, native. 25 measured samples per case
per block, **50 per arm/size, 450 final samples**. No concurrent local test runs.

Latency is median wall time. CPU is mean per-process CPU, measured separately for
Python and its PostgreSQL backend; Windows CPU accounting has roughly 15.625 ms
granularity, so single samples are not useful. Database statistics are per-operation
means. SQL/DML/commit counts are deterministic. All transactions = explicit Django
commits plus statements executed in autocommit; this exposes the transactions that
an explicit-commit-only count misses. It does not count measurement queries.

Statement WAL is pg_stat_statements' WAL attribution, not total cluster WAL or
commit-record WAL. Buffer hits measure database activity, not distinct pages or
physical I/O. Reads are nearly all warm-cache. Statistics instrumentation adds
overhead equally to each arm. These are local persistence costs with a fake
provider, **not production throughput or WAN-database latency**.

Host variability matters. In the final run, nine-chunk block median latencies were
219.49 / 207.59 ms for ordinary Due and 234.34 / 208.03 ms for bounded settlement.
The five-chunk first native block was an outlier at 142.14 ms versus 79.05 ms in
the reverse block. Preserve both blocks rather than selecting favorable runs.
The small two/five-chunk median differences do not establish a repeatable win.

## Final results

Lower is better for costs below. Counts and latency are medians; CPU is mean.

| Chunks / recipients | Arm | SQL | DML | Explicit commits | All transactions | App CPU ms | DB backend CPU ms | Latency ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 / 51 | bounded | 56 | 17 | 8 | 28 | 37.81 | 27.50 | 65.34 |
| 2 / 51 | native | 40 | 13 | 7 | 14 | 21.56 | 15.31 | 40.81 |
| 2 / 51 | ordinary | 57 | 17 | 9 | 29 | 39.69 | 20.94 | 66.55 |
| 5 / 201 | bounded | 110 | 38 | 17 | 61 | 92.19 | 39.69 | 133.92 |
| 5 / 201 | native | 73 | 28 | 16 | 29 | 70.62 | 31.88 | 87.49 |
| 5 / 201 | ordinary | 114 | 38 | 21 | 65 | 96.88 | 50.00 | 136.79 |
| 9 / 401 | bounded | 183 | 66 | 30 | 106 | 155.94 | 71.56 | 216.91 |
| 9 / 401 | native | 117 | 48 | 28 | 49 | 100.94 | 32.19 | 125.27 |
| 9 / 401 | ordinary | 190 | 66 | 37 | 113 | 150.94 | 62.50 | 210.07 |

| Chunks | Arm | Plan ms | Execute ms | Buffer hits | Statement WAL bytes |
| --- | --- | ---: | ---: | ---: | ---: |
| 2 | bounded | 5.19 | 3.21 | 1006 | 16961 |
| 2 | native | 2.45 | 1.72 | 439 | 8778 |
| 2 | ordinary | 5.22 | 3.23 | 863 | 17344 |
| 5 | bounded | 10.64 | 8.55 | 2162 | 50819 |
| 5 | native | 5.33 | 5.21 | 1357 | 27308 |
| 5 | ordinary | 12.22 | 9.17 | 2152 | 50236 |
| 9 | bounded | 16.61 | 15.63 | 4253 | 97219 |
| 9 | native | 6.20 | 6.73 | 2880 | 51942 |
| 9 | ordinary | 15.84 | 13.91 | 4217 | 96142 |

For nine chunks, the prototype removes seven statements and seven transactions,
but leaves all 66 DML statements and approximately 97 KB statement WAL. Native
uses 48 DML statements and approximately 52 KB statement WAL. This is why sharing
completion commits alone cannot close the gap. The remaining work includes
independent per-row claims, provider-entry guards, receipt writes, diagnostics,
settlement markers and application relationship/advisory-lock reads.

## Initial prototype

`bounded-bench-*.jsonl.gz` retains the first experiment, whose grouped lock query
combined N complete querysets. Its runtime was `d533436`, mail `55203cc8d`.
The final experiment uses one primary-key lookup and validates the exact captured
token, state and revision under lock, using the same authority predicate as ordinary
completion. This reduces query construction; it still does not establish a
consistent end-to-end win. Do not pool the initial and final samples.

## Safety and proofs

Existing single-row API semantics stay unchanged. The opt-in path buffers only
successful completions after each receipt has independently committed. Visibility
is delayed until a count-based boundary; hooks can be retried after a group
rollback; lock spans grow; persistent hook failures remain CLAIMED until registered
recovery. These are explicit tradeoffs, not claimed semantic equivalence.

Final focused run: 33 passed, 272 deselected, including 15 direct PostgreSQL cases
and generated bounded contract cases. The reports here are **selected-case evidence**,
not a claim that every declared profile executed in that run. The prior wider
bulk/runtime run passed 1,047 tests before the final lock-query refinement; final
full-suite evidence belongs to the linked PR's exact-head CI.

Generated pytest-obligation proofs cover lost completion buffers, repeated recovery,
deaths between settlement-group commits, both inline and separate receipts, and
sensitivity controls for disabled recovery and duplicate provider effects. Direct
tests cover failed hooks/healthy-row isolation, death inside an atomic group,
provider failure, all three authority fields, invalid bounds, model save/signals
and soft time limits. The soft-limit test failed before the fix.

No new recovery runner or weakened proof was added to pytest-obligation. Its
existing proof rejected an initially incomplete single-commit history because
recovery had nothing left to do. The corrected history has two real settlement
groups, so death after the first commit must recover the second group's receipts.

## Reproduction

Use the pinned backend worktrees and the ordinary runtime for native/ordinary,
copy the four public prototype runtime modules into the benchmark variant, apply
the narrow mail patch, and commit the candidate so the harness refuses dirty
sources. `prepare_bounded_bench.py` records the original setup; adjust local paths.
The disposable PostgreSQL cluster listens on 56449 and preloads pg_stat_statements.
`run_bounded_arm.py` loads checked-in dummy settings and generates an ephemeral
test RSA key in memory. No credential material is archived.

```powershell
$env:BENCH_PREFIX = 'bounded-final'
backend/.venv/Scripts/python.exe run_bounded_bench.py
backend/.venv/Scripts/python.exe summarize_bounded.py
```

Raw traces are gzip-compressed JSONL. The harness records source SHA, dirty-diff
hash, harness hash, statement text without parameters, CPU and database counters.
This branch is evidence-only and must not be merged into the shipping package.
