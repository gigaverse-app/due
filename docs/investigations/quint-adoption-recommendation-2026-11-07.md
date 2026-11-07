# Quint adoption recommendation for Due and pytest-obligation

Date: 2026-11-07. Status: proposal for review.

## Recommended decision

Adopt Quint as an executable specification and model-based testing tool alongside
the Python implementations. Build a small Python conformance bridge first, using
one report-evidence machine from pytest-obligation and one claim/settlement
machine from Due. Evaluate code generation only after those pilots establish
useful correspondence between model behavior and real implementation behavior.

Keep Django, PostgreSQL, Celery and pytest as the execution infrastructure. A
restricted generated Python transition core or an embedded Quint decision engine
could be evaluated later; neither is recommended as an immediate rewrite.

This document proposes architecture and acceptance criteria. It does not introduce
a runtime dependency, compiler, bridge API, test lane or production change.

## What the audit established

The audit combined source inspection, executable Quint models and Python boundary
reproductions. Quint did not ingest the repositories and automatically discover
their bugs. Some models encoded hypotheses found through source review; the
implementation reproductions established whether those histories applied to code.

The following independently reviewable fix PRs provide concrete examples. They
were open, not merged, when this recommendation was prepared. Their descriptions
and checks carry the current validation evidence; this document does not assert
that every required CI job has passed.

| Finding | State-modeling lesson | Fix proposal |
| --- | --- | --- |
| Incomplete test execution pruned unmeasured cost baselines | Absence of evidence is not evidence that a contract was retired; session termination and successful complete execution are different states. | [pytest-obligation #208](https://github.com/gigaverse-app/pytest-obligation/pull/208) |
| An ini-configured host survived into a later embedded pytest session | A process-global value needs explicit ownership and restoration when configured for a session. | [pytest-obligation #209](https://github.com/gigaverse-app/pytest-obligation/pull/209) |
| A rejected report shard partially changed retained evidence | Validation failure must leave the aggregate unchanged, including when the incompatibility is discovered late. This was not evidence of a silently successful whole xdist run. | [pytest-obligation #210](https://github.com/gigaverse-app/pytest-obligation/pull/210) |
| Reference-provider allocation reused a surviving resource's identifier after deletion | Current collection size is not a monotonic identity allocator. The affected code is a shipped reference fake, not external provider persistence. | [pytest-obligation #211](https://github.com/gigaverse-app/pytest-obligation/pull/211) |
| Manual README reporting accepted ineligible source evidence | Artifact shape and a successful download do not establish source provenance, completeness or revision identity. | [Due #198](https://github.com/gigaverse-app/due/pull/198) |

Two Due timestamp-token candidates remain conditional. Fixed-clock, fake-ORM
histories show that a repeated token can let a stale caller affect a successor.
They do not establish an ordinary production history or PostgreSQL concurrency
failure. In the observation path, normal positive pacing rules exclude the simple
immediate successor history. These candidates need reachable boundary histories
and an API/schema decision before a runtime fix is justified. They must not be
confused with Due's separately captured UUID/state/revision claim fence.

The audit also found defects in its own draft models: unreachable scenarios,
overwritten stale-worker snapshots, properties true by construction, reserved-name
errors, and missing next-state assignments. Source review corrected assumptions
such as treating lease expiry alone as loss of every settlement authority. A
passing model is useful evidence only after its correspondence and properties are
reviewed. Current family inventories and bounded checks are not exhaustive
source-refinement proofs or a completed proof of all implicit state machines.

## Quint's role and limits

Quint is an executable specification language. Its tools typecheck models, run
scenarios, simulate histories and invoke model checkers. The simulator explores
sampled executions: no counterexample found is not an exhaustive proof. A model
checker result must likewise be reported with its bounds and assumptions.

Quint's evaluator executes model state changes. A modeled database write does not
write PostgreSQL; a modeled broker effect does not publish a Celery message. Python
integration requires a driver or another explicit implementation boundary.

The audit used Quint 0.33.1. Its inspected CLI offers TLA+ and JSON compilation
targets, not a Python target. A Python generator would be a new maintained tool,
not an existing capability assumed by this proposal. Quint Connect currently
documents a Rust model-based testing bridge and is a useful architectural example,
not a Python integration we have already deployed.

Sources: [Quint's purpose](https://quint.sh/docs/what-does-quint-do),
[simulator semantics](https://quint.sh/docs/simulator),
[model-based testing](https://quint.sh/docs/model-based-testing),
[Quint Connect](https://github.com/quint-co/quint-connect),
[versioned CLI source](https://github.com/quint-co/quint/blob/v0.33.1/quint/src/cli.ts).

## Match Python and Quint at behavioral boundaries

For each machine, maintain an explicit mapping with three parts:

| Mapping | Due claim example | Obligation report example |
| --- | --- | --- |
| Model action to production operation | Claim, reap, retry or complete through the real Python API. | Submit a shard to the real report merge API. |
| Concrete state to abstract state | Project committed status, revision and token equality relationships. | Project declarations, retained evidence and computed verification. |
| Environment choice to controlled input | Inject clock changes, broker failure or worker interruption at named boundaries. | Supply incompatible declarations or incomplete execution evidence. |

Use model-generated histories to drive the implementation, then compare projected
state and observable outcomes after each specified boundary. In mathematical
terms, an implementation step must correspond to an allowed model transition
under the chosen abstraction. Internal steps may leave the abstract state
unchanged. A deterministic model can provide exact next-state expectations;
a nondeterministic model requires checking allowed outcomes or explicitly binding
its environment choices to the real execution.

The bridge must preserve the distinctions that matter:

- Compare committed state at transactional boundaries; separately model crash
  points where effects can escape. Do not compare an arbitrary intermediate ORM
  state with an atomic model transition.
- Preserve identity equality, inequality and reuse. UUIDs can be renamed to model
  symbols only through a consistent mapping; renaming every occurrence to a fresh
  symbol would hide repeated-token defects.
- Make clock assumptions, expiry boundary comparisons, retries and scheduler
  choices explicit. Passing serial traces alone does not exercise concurrency.
- Exercise real decisions. Fault injection may replace a provider or transport,
  but must not replace the production ownership or merge predicate being tested.
- Test rejected operations as well as permitted histories. Valid-trace replay
  alone does not establish that stale completions or invalid shards are refused.
- For concurrent histories, use controlled interleavings and observe commit and
  effect boundaries. Where multiple operation orderings are valid, compare against
  the allowed orderings rather than demanding a particular scheduler sequence.

Persist model/source revisions, tool version, bounds, seeds, selected actions and
the resulting trace with a failure. Use immutable model snapshots during checks
so edits made while a test runs cannot be confused with the validated input.

## Two initial pilots

### Obligation report evidence

Bind a small model to the actual report merge and verification APIs. Generate
compatible and incompatible shards, repeated reports, missing phases and late
declaration mismatches. Observe both return/exception behavior and the retained
aggregate, including object identity where that is part of the API contract.

Acceptance requires that a late rejected shard leaves the aggregate unchanged,
compatible evidence still merges, and a deliberately broken mutation that updates
before validating is detected. Test the composer and pytest/xdist boundaries
separately; correctness of the pure merge function does not establish correct
report collection or trustworthy input provenance.

### Due claim and settlement

Use real PostgreSQL transactions and the production claim, recovery and completion
APIs. Preserve both workers' captured tokens. Include expiry without reaping,
expiry followed by reaping and successor claim, revision supersession, duplicate
delivery, and stale completion with an observable finalizer.

Acceptance requires that stale workers cannot enter the product finalizer or
alter a successor's authority, while an expired but unreaped owner retains the
behavior actually specified by the implementation contract. A mutation removing
the token/revision fence must be caught. Keep provider effects and broker
notifications independently observable; database-only agreement is insufficient.

Any implementation of these pilots must follow the existing
[conformance requirements](../../package/docs/conformance.md) and
[development validation rules](../../CONTRIBUTING.md), including independent
enrollment, generated failure cases and executed evidence where applicable.

## Which code should be generated

| Artifact | Recommendation | Reason |
| --- | --- | --- |
| Event/state types and trace codecs | Generate after settling the mapping. | Reduces schema drift and repetitive conversion code. |
| Test discovery, scenario parametrization and adapter interfaces | Generate early where useful. | Makes model coverage explicit without generating expected behavior from production code. |
| Pure deterministic transition functions | Evaluate after the pilots. | Can share checked and executed decision logic. |
| ORM transactions, locks, provider calls and broker delivery | Keep explicit and independently tested. | Their atomicity, crash and retry semantics are part of the correctness argument. |
| General Quint-to-Python translation | Do not start here. | Creates a compiler project before demonstrating the value of the bridge. |

If a transition generator is justified, define a strict executable subset. Use
explicit events and deterministic decisions, returning proposed next state and
effects. Reject unsupported constructs rather than silently selecting a branch
or approximating semantics. Keep model-only scheduling, fault injection and
temporal properties outside the generated production core.

Specify numeric, collection, ordering, missing-value and identity semantics.
Emit reviewable Python with source mappings and reproducible generation. Test
the generator by comparing Python evaluation with Quint on the same states and
events, retaining independent invariants and hand-written regressions. Agreement
between generated code and its source model establishes translation confidence,
not that the shared business policy is correct. No generator is trusted merely
because it successfully translates its own examples.

## The case for and against a Quint runtime

The strongest case for runtime use is a small decision core whose checked
transition definitions are the same artifact executed in production. This can
reduce specification drift, enforce explicit state/effect boundaries and improve
replay. Executing one concrete transition is a different workload from exploring
an entire state space; model-checking expense does not demonstrate that such a
core would be too slow.

The strongest case against is that Due's difficult correctness obligations live
at the database, process and provider boundaries. A correct decision against a
stale snapshot can still produce an incorrect write. The host must revalidate
authority transactionally, persist effect intent where needed, and handle crashes
and uncertain outcomes. An embedded evaluator also adds deployment, migration,
availability and debugging obligations. The original compact model can become
harder to analyze as production details accumulate.

Quint's [Choreo](https://quint.sh/docs/choreo) demonstrates explicit next-state and
effect representations for protocol specifications. It is architectural inspiration,
not evidence that our production persistence and delivery requirements are solved.

If runtime use remains attractive after the pilots, first run it in shadow mode:
feed the same recorded inputs to Python and Quint, compare decisions and retain
Python as the authority. Record model version and environment inputs so that a
disagreement is replayable. Do not execute proposed effects twice. Promotion would
require a separate review of discrepancies, transactional integration, operational
failure policy and state/version migration.

## Adoption stages and decision gates

1. **Establish correspondence.** Implement the two pilots, source mappings and
   independent negative controls. Gate: known pre-fix defects are caught, corrected
   behavior passes, and the controls demonstrate faults reached real decisions.
2. **Make checks reproducible.** Pin tooling, retain immutable inputs/traces and
   document bounds. Run fast typechecks and deterministic scenarios in affected
   CI lanes; place broader simulations/model checking in explicit larger runs.
   Gate: the selector declares actual model/adapter inputs, and artifacts clearly
   distinguish sampled checks from verification. Preserve documentation-only
   selection behavior.
3. **Generate repetitive interfaces.** Add types/codecs and harness glue only
   where the pilots expose duplication. Gate: deterministic generation, coverage
   checks for unmapped actions/fields, and no semantic behavior hidden in adapters.
4. **Evaluate executable decisions.** Compare handwritten Python, generated Python
   and, if warranted, an embedded evaluator for one pure machine. Gate: semantic
   agreement plus independent regressions, maintainable generated output and
   measured operational cost. Production adoption needs a separate decision.

Measure evaluator startup separately from steady-state transition latency,
serialization and database time. Benchmark representative state sizes and report
throughput, tail latency, memory and growth with collection size. Set acceptance
budgets from the selected workload before promotion. Small scenario timings from
the audit are not a production-runtime benchmark.

The immediate review decision is whether to approve the two conformance pilots
and their acceptance criteria. That does not approve a general compiler or a
production runtime migration. The unresolved choices are bridge ownership and
maintenance, the exact abstraction boundaries, CI cost budgets, and whether the
pilots demonstrate enough duplicated decision logic to justify generation.
