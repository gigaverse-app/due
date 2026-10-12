# Widget profiler quality

The previous nonblocking captures lost stack reads and the blocking collector
could race the traced child's exit. This follow-up changes instrumentation only.

Validation:
- Reproduce collector exit failures independently of the applications.
- Require complete Due and DBOS journeys, zero sampling errors, no lag,
  sample-count reconciliation and minimum application/web/worker coverage.
- Negative controls must reject each incomplete or unusable capture.
- Repeat CPU captures for pre-change Due and current Due against pinned DBOS.
- Capture all-thread wait stacks separately to explain connection-loss recovery.
- Publish raw artifacts, source/command/time provenance and interpretation;
  keep every profiled duration outside performance claims.
