# What the demo migrations establish

The ports exercise application outcomes as well as queue completion. Their
provenance remains pinned so added product behavior is visible in comparisons.

| Reference | What the original demonstrates | What the Due port additionally checks |
| --- | --- | --- |
| [Procrastinate Django books at 8279a61](https://github.com/procrastinate-org/procrastinate/tree/8279a61789fdf8227d6f513ad15e4b26f1b78b49/procrastinate/demos/demo_django) | Save a book, simulate indexing with a five-second delay, enqueue a second task that sets `indexed=True` | Real SQLite index content, atomic product/work admission, fenced successor admission, external activation and the product pointer after process death |
| [Temporal updatable timer at ff0f134](https://github.com/temporalio/samples-python/tree/ff0f1343822e532a5c377f02003002d2dcac4f79/updatable_timer) | Wait until a durable deadline, change it through a signal, query its current value; the test uses Temporal's time-skipping environment | A Django publication, immutable external bytes, cancellation, caller submission identity, late-worker fencing and recovery after the external effect but before settlement |

The original Procrastinate view saves before deferring. That is a property of
that sample: Procrastinate's Django connector supports atomic enqueueing. Our
[matched implementation](../examples/comparison/README.md) uses it and also has
stable provider keys, retries and stalled-job recovery. Both implementations
must pass the shared real-process crash and rollback matrix. Due's advantage in
this comparison is its explicit product/work binding and generated application
proofs, not exclusive ownership of transactions or crash recovery.

The original Temporal workflow returns when its timer finishes; it performs no
external publication. File publication costs and correctness are therefore
properties of the Due demo's added provider. An equivalent Temporal activity
could use the same provider. The earlier
[deadline comparison](https://github.com/gigaverse-app/due/blob/54aba8e3ba93ef100578ec8ffa5897d4c95071c8/evidence/scheduled-temporal/deadline-improvement/README.md)
measures local scheduling lateness, not production engine throughput.

## Improvements prompted by the ports

A crash history that merely agrees with a normal run can preserve the same bug
in every run. `pytest-obligation.ProcessHistory.expected` supplies an independent
product requirement, using the same oracle as handoff histories. Due requires
that declaration for `EffectExamples.process_handoffs`. Books require one
searchable, correctly authored book and one successor; scheduled publications
require the published status, the exact provider bytes and one deadline row.
The generated tests run actual child processes at named boundaries and recover
through registered production code. Negative controls deliberately return wrong
provider content and must fail even though the work settles successfully.

These are executable proofs of the declared scenarios and observations. Their
strength depends on observing the correct external system and defining an
independent expectation; a constant or self-fulfilling observer cannot establish
arbitrary application correctness. CI requires the named process cases as well
as complete profile reports, so deleting a binding cannot silently remove them.

The shared file provider previously overwrote a key on every replay. That could
truncate already published bytes, and reusing a key with different content could
replace an immutable revision. It now compares existing content, stages a new
file, and atomically links complete bytes into place without replacing a winner.
Equal retries are reads; conflicting retries fail. Concurrent writers and death
during staging are covered, and the application histories cover death after
publication but before database settlement. Provider call counters still count
requests, independently of whether the provider actually writes bytes.

## Costs and remaining boundaries

- Due's books port has two explicit work tables and more declarations than the
  shared Procrastinate job table. Its deployment also uses Redis, Celery and Beat;
  Procrastinate's example needs PostgreSQL and a worker. Shared file/index proof
  bindings reduce repeated test setup but do not remove this operational cost.
- First file publication needs one exclusive staging file and atomic installation.
  It no longer allocates and recursively cleans a directory for each revision. It can cost
  more than a direct write; retries avoid rewriting bytes but still read and
  compare the payload. This is a correctness/resource tradeoff, not a claim that
  every operation is faster.
- The file provider assumes an exclusively owned local filesystem supporting
  hard links. It does not provide distributed object-store semantics or a
  power-loss durability guarantee. A killed writer can leave a hidden staging
  file; live writers must be quiesced before removing stale staging data.
- Deadline hints remain transient optimizations. Persisted product/work identity
  and periodic recovery own liveness. This change adds no SQL tables, rows or
  writes and does not change that recovery mechanism.
- Temporal still provides workflow history and its own timer/testing environment.
  Due's application contracts are useful in an existing Django/Celery deployment;
  they do not replace those workflow-engine facilities.

### CI capture layout

The Widget Store workflow keeps ten captures: each baseline/candidate revision gets
forward and reverse browser benchmarks, two five-sample CPU profiles, and one
two-sample wall profile. Each capture owns a runner, PostgreSQL and Redis; profiling
never competes with a benchmark or another capture. The ten one-second business
stages, warmup, failed payment, crash restart, and connection-loss journeys remain
unchanged. Parallel captures reduce elapsed CI time at the cost of repeated setup.

Benchmark artifacts retain `widget-store-{revision}-reverse-{false|true}` names.
Profile artifacts append `-cpu-1`, `-cpu-2`, or `-wall` to the corresponding
`reverse-false` name. Their internal `profile/{capture}/` paths and `quality.json`
gates are unchanged. Download all ten artifacts for the complete evidence bundle.

Automatic PR events reuse a complete successful bundle from the same PR for up to
24 hours when CI-esta verifies the exact tested merge tree, base, head tree, and
shared tooling. A label change can therefore finish without launching ten more
runners. Changed trees, missing evidence, later failures, manual reruns, `test:full`,
and main builds execute the captures again. The `Widget comparison gate` records
proof only after all ten captures succeed; reused runs cannot refresh its lifetime.
Reused runs have no new capture artifacts. Download the bundle from the original
successful PR run within the last 24 hours; its gate has the same `Validated inputs`
receipt as the new run's evidence lookup.

The pinned Yappi 1.7.6 collector reads flat counters in one enumeration, retaining
per-thread identities and the same duplicate-source merging as the public collector.
It avoids constructing call graphs that the JSON output never used. Real CPU/wall
parity tests cover every counter, and fork plus periodic-snapshot tests check that
capture continues through application work and child generations.

Live snapshots can contain an impossible negative function time. The validator
keeps the raw row, lists it in `quality.json` under `timing_anomalies`, and excludes
it from summary timings, call totals and business-coverage proof. It does not
replace the value with zero. Every process still needs usable timing, and every
crash generation still needs valid business-function coverage. Nonfinite timing,
invalid call counts, missing processes and incomplete journeys remain failures.
