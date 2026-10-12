# Widget profiler quality

Instrumentation only. Blocking py-spy stalled DBOS even at 25 Hz; nonblocking
sampling lost too many reads. Native perf retained CPU samples but CPython JIT
metadata was incomplete for prefork children. Use pinned in-process Yappi for
repeatable function attribution, keeping instrumented timings out of benchmarks.

Validation:
- Complete both applications' checkout, failed-payment, crash and SQL-loss journeys.
- Require every supervised process and business-function coverage before and
  after crash, including actual prefork delivery rather than just worker startup.
- Reject snapshot errors, missing processes/generations, clock mismatches,
  invalid counters and orphaned process ancestry.
- Test real threads and forks: no main-thread double counting, inherited counters
  or child overwrite of a parent's snapshot.
- Repeat CPU profiles for baseline/current Due against pinned unmodified DBOS;
  collect wall-clock profiles separately to locate reconnect waits.
- Publish source/command/time provenance, raw CI artifacts and interpretation.
  State missing final snapshot intervals and overlapping/incomplete wall totals.
