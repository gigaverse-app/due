# Quint adoption recommendation for Due and pytest-obligation

Date: 2026-11-07. Status: proposal for review.

## Recommended decision

Adopt Quint as an executable specification and model-based testing tool alongside
the Python implementations. Build a small Python conformance bridge first, using
one report-evidence machine from pytest-obligation and one claim/settlement
machine from Due. Evaluate code generation only after those pilots establish
useful correspondence between model behavior and real implementation behavior.
Use those pilots to evaluate a general pytest-quint layer, reusable contract
packages and a contract-governed agent workflow. Broader options and their
existing Quint foundations are compared below.

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

## Making Quint central to development

The broader opportunity is to make an executable behavioral contract connect
design, agent-written implementations, tests, optimization and observed execution.
The following approaches build on existing Quint tools; the proposed Python
package names are architectural labels, not claims of available packages.

### 1. A general Python conformance layer: pytest-quint

Create a language-neutral contract-to-implementation testing core with a Python
driver and pytest integration. Bind model actions to real application commands,
project independently observed state back into the model, and retain replayable
failures. Support rejected operations and implementation-originated histories as
well as model-generated valid histories. Minimize failures only while preserving
the history's prerequisites and reproducing the discrepancy.

This would be the pytest-obligation analogue for arbitrary Quint specifications.
The distinction is contract ownership: pytest-obligation supplies durable-work
contracts, while this layer accepts developer-authored or library-supplied Quint
contracts. Both require explicit bindings. Being framework independent does not
make pytest-obligation a general specification language; a parser, authorization
policy or cache protocol may require different contracts.

**Existing foundation:** [Quint Connect](https://github.com/quint-co/quint-connect)
already generates traces, drives Rust implementations, compares state and
supports reproducible seeds. Python would extend that approach, not invent
model-based testing.

**Additional work:** pytest fixtures and isolation, async execution, database and
concurrency adapters, trace conversion, missing-binding errors, independent
observations, fault controls and useful diagnostics. Reuse pytest-obligation
capabilities where they fit; do not require arbitrary Quint models to pretend
they are durable-work domains. Start with the two pilots below before deciding
whether a standalone package is warranted.

**Benefit:** agents get concrete behavioral feedback and maintainers detect
implementation drift in exercised histories. Neither a passing model alone nor
a passing bridge proves unrestricted implementation equivalence.

### 2. An agent workflow governed by reviewed contracts

A feature change should produce an explicit behavioral change, model checks,
implementation edits and conformance evidence. Reviewers can examine assumptions,
invariants and counterexamples before assessing a larger generated code diff.
Source mappings help locate affected behavior, but a source edit alone does not
establish semantic drift.

**Existing foundation:** the [Quint LLM Kit](https://github.com/quint-co/quint-llm-kit)
provides modeling and implementation assistance. The Quint team's
[Malachite case study](https://quint.sh/posts/llm_era) describes specification
changes, human validation, agent-assisted implementation and model-based testing.
It is a reported case study, not a general productivity benchmark or compiler
correctness result.

**Additional work:** repository policy for contract ownership, reviewed assumption
changes, action-to-code mappings, retained evidence and independent negative
controls. An agent must not silently weaken the expected behavior to make its
implementation pass. Humans must still judge whether the contract captures the
requirement: model and implementation can agree on the same mistake.

**Benefit:** a stable, executable target for implementation and debugging, with
less repeated interpretation of prose across agent tasks.

### 3. Reusable contract packages

Package common behavioral requirements such as stale-owner fencing, harmless
replay, atomic rejection and monotonic publication. Include the model,
environment assumptions, binding interfaces, example histories and intentionally
broken implementations that demonstrate the checks detect violations.

**Existing foundation:** [Choreo](https://github.com/quint-co/choreo) provides
reusable protocol-modeling abstractions for messages, local state, transitions
and environmental effects. pytest-obligation already packages implementation
contracts for durable work.

**Additional work:** versioned contract distributions, Python/framework bindings
and conformance evidence. Distinguish model-building abstractions from complete
adoption contracts. Check composition explicitly: two individually conforming
components can violate each other's environment assumptions.

**Benefit:** maintainers and agents reuse reviewed requirements instead of
repeatedly inventing ownership, retry or recovery semantics.

### 4. CI driven by dependencies and reusable evidence

Track the specifications, implementation code, adapters and environment inputs
each check depends on. Replay known regressions first, run affected conformance
checks on changes, and schedule broader exploration separately. Cache model
results only when the relevant model inputs, tool versions, options, bounds and
assumptions remain unchanged. Implementation evidence also depends on application
and adapter revisions.

**Existing foundation:** Quint provides deterministic scenarios, simulation,
model checking, seeds and trace output. The
[CLI](https://quint.sh/docs/quint) exposes TypeScript and Rust simulation
backends. These are ingredients, not an application dependency graph.

**Additional work:** conservative selection, cache invalidation, trace corpus
management and reporting. Missing dependency knowledge must broaden checks.
Select diverse histories for costly integration tests without claiming that the
selection retains all possible defects.

**Benefit:** potentially faster feedback and less repeated work. Adoption may
initially increase CI time. A passing model never substitutes for integration
checks of omitted behavior. Measure time-to-failure, total CI cost and faults
detected at stated budgets before claiming an improvement.

### 5. Contract-guided optimization of implementations

Retain a simple behavioral specification while experimenting with batching,
indexes, caching, allocations or concurrency. For Due, a candidate is replacing
per-row operations with batches while preserving ownership, settlement and
failure semantics. The real adapter must expose partial failure and transaction
boundaries instead of hiding them behind one apparently atomic operation.

**Existing foundation:** Quint's model exploration and implementation testing can
already support this workflow. No production Quint evaluator is required.

**Additional work:** a repeatable pipeline combining conformance checks, real
concurrency controls, representative workload benchmarks and performance
regression gates. Behavior-preserving optimization and intentional protocol
changes are separate decisions; changes to assumptions need review.

**Benefit:** agents can search for faster implementations against an explicit
behavioral target. The implementation supplies the speed; Quint supplies part
of the evidence that behavior was preserved. Sampled correspondence is weaker
than a formal refinement argument.

### 6. A compiled or embedded decision core

Use one restricted artifact for both model decisions and production transition
logic: state plus event yields next state plus requested effects. Generate
Python first if justified; evaluate native compilation only for a demonstrated
workload. Persistence, networking and effect execution remain host responsibilities.

**Existing foundation:** Quint has evaluators and Choreo represents state/effect
transitions. The [Rust simulator project](https://quint.sh/posts/quint_deserves_rust)
targets faster specification exploration. That is distinct from a supported
production Python compiler or evidence of faster application execution.

**Additional work:** an executable subset, deterministic decision semantics,
numeric and collection representations, effect interfaces, migrations and
compiler validation. Environmental nondeterminism becomes explicit input; a
compiler must not arbitrarily choose an outcome that the model merely permits.
The generation and runtime sections below specify further gates.

**Benefit:** less duplicated decision logic and possible performance gains.
Conversion, startup and hosting overhead can also make this slower. Benchmark
the complete boundary and distinguish a tested compiler from a proven one.

### 7. Observed execution feeds back into the contract

Capture relevant staging or production events and observations, validate whether
their histories are permitted, and turn reproducible discrepancies into regression
inputs. Version the contract with the trace.

**Existing foundation:** Quint documents
[trace validation](https://quint.sh/docs/model-based-testing) as the reverse of
model-generated implementation testing.

**Additional work:** instrumentation, causal ordering, privacy controls,
observation completeness and diagnosis. Missing observations or uncertain ordering
must yield an inconclusive result where appropriate. A discrepancy can originate
in implementation, model or instrumentation; it is not automatically an
application defect. Finite traces also do not establish unrestricted eventual
progress.

**Benefit:** real histories expose assumptions missed in laboratory tests and
give agents concrete debugging inputs.

### 8. LemmaScript-style source verification for Python

Evaluate a complementary path that verifies selected production Python functions
from their own annotated source, beginning with the existing tools below. Python remains the code that runs; a tool extracts
verification artifacts from that code and its contracts. This reverses the
direction of the generated-core proposal: implementation to proof artifacts,
rather than specification to production code.

**Existing foundation:** [LemmaScript](https://github.com/midspiral/LemmaScript)
is a TypeScript verification toolchain, labeled a tech preview. Its
[design](https://github.com/midspiral/LemmaScript/blob/main/DESIGN.md) and
[current specification](https://github.com/midspiral/LemmaScript/blob/main/SPEC.md)
describe source annotations for preconditions, postconditions and loop
invariants, with Dafny or Lean/Velvet verification artifacts. The ordinary
TypeScript remains the production source. Generated definitions and user/agent
proof additions have separate regeneration rules; its CI workflow checks proofs
and stale generated artifacts. These are documented capabilities inspected for
this recommendation, not independently rerun LemmaScript experiments.

Python tools already offer variants of this architecture. Comments or sidecar
contracts can attach to typed functions while keeping proof annotations out of
runtime execution:

```text
production Python + function contracts
          |
          +--> ordinary Python execution
          |
          +--> supported-subset extraction --> verifier artifacts
                                                 + proof additions
                                                        |
                                                 checked obligations
```

This is distinct from pytest-quint's sampled implementation histories and does
not require a Quint production runtime. Quint would remain useful for protocol
histories, concurrency and environment assumptions. A Python verifier would
check local function properties, such as a pure merge returning unchanged
evidence on rejection or an allocator returning an identity outside the live
set. Relating those properties to a Quint action requires an explicit abstraction
and refinement obligation: under the stated preconditions, the projected Python
result must be an allowed model result. Proving a local postcondition alone does
not establish that connection.

**Integration requirements:** evaluate each candidate's supported Python fragment
and semantics for integers and floats, collection ordering, equality versus
identity, aliasing, mutation, exceptions and termination. Start with small typed pure functions;
reject unsupported selected code unless a reviewed abstraction or external
contract explicitly accounts for it. Arbitrary ORM, async and dynamic Python
must not silently become pure mathematical functions.

The trusted boundary includes the Python-to-verifier translation, external
contracts, verifier/backend assumptions and the execution semantics. Running the
same Python source removes a separately maintained implementation copy; it does
not eliminate translation risk. LemmaScript itself exposes assumptions, externs
and abstractions in its current specification. A Python design should inventory
such assumptions and exclusions in each result. Separate discharged obligations,
trusted assumptions, timeouts and unsupported code. Regenerate from exact source
revisions in CI, preserve proof additions, and fail on stale artifacts or
unresolved obligations rather than accepting proof placeholders.

**Benefit for agents:** agents can propose code, loop invariants and helper
lemmas, then use verifier diagnostics to repair them. They may also propose
contract changes, but those require explicit review; changing a precondition or
adding an assumption must not silently turn an incorrect implementation green.
The verifier checks proof attempts, while reviewers assess the requirements and
the trusted boundary.

**Runtime and CI effects:** offline proof artifacts need not add runtime
overhead; optional boundary guards still have a cost. This does not inherently
make Python faster, but checked equivalence or preservation properties could
support safer optimization. Proof checking can be expensive. Cache only with
complete source, contract, dependency and toolchain inputs, and keep integration
tests for behavior outside the verified fragment.

**Existing Python tools: evaluate before building.** A broader source survey
found a close implementation of the proposed architecture in VeriPy. The
recommendation is therefore to evaluate existing verifiers and integrate an
appropriate one with Quint contracts and pytest evidence before considering a
new Python frontend. The comparison below is based on upstream documentation and
repository inspection on 2026-11-07; none of these tools was installed or its
proof results independently reproduced for this report.

| Tool | Mechanism and relationship to LemmaScript | Scope and evaluation implication |
| --- | --- | --- |
| [VeriPy](https://github.com/astrio-labs/veripy) | Preserves admitted Python bodies with `#@` contracts, generates Dafny or Lean artifacts and checks separate proof support. Includes agent diagnostics and old/new compatibility checking. Closest architectural match found. | A research tool supporting selected fragments. Its frontend correspondence is not mechanized end to end. First candidate for the source-preserving proof and maintenance experiment. |
| [Nagini](https://github.com/marcoeilers/nagini) | Verifies typed Python through Viper, with contracts and specifications for functional and other properties. Has an established research history and documented MCP verification interfaces. | Evaluate supported language/heap semantics and annotation effort against our exact function. It is an alternative verification infrastructure, not a Dafny/Lean emitter. |
| [Apex](https://github.com/hyperionaidev/apex) | Combines agent integration, Lean proofs about translated functions and counterexample search; records different strengths of evidence. | Its documented proof-supported Python subset is small. Translation is trusted and differential execution is testing evidence, not proof of correspondence. Secondary candidate for agent workflow ergonomics. |
| [CrossHair](https://crosshair.readthedocs.io/en/latest/introduction.html) | Runs Python with symbolic inputs and searches for contract violations, including contracts expressed through supported assertion/docstring/decorator forms. | Useful complementary counterexample search. Absence of a counterexample is not a guarantee; do not report it as a discharged deductive proof. |
| [Proofside](https://pypi.org/project/proofside/) | Separates mathematical intent, optional agent-proposed contracts, human-reviewed contract selection and Nagini/Viper checking. | A deliberately narrow sidecar for self-contained typed mathematical functions and restricted arithmetic contracts; useful workflow precedent rather than a general application verifier. |

VeriPy's [README](https://github.com/astrio-labs/veripy/blob/main/README.md)
documents a minimal example:

```python
#@ ensures result == x + 1
def increment(x: int) -> int:
    return x + 1
```

Its compatibility checker is especially relevant to drift. Two versions can
satisfy the same underspecified contract while returning different values.
VeriPy separately compares whether the new component admits the old inputs and
preserves declared observations, including modeled exceptions. Its
[assurance argument](https://github.com/astrio-labs/veripy/blob/main/docs/ASSURANCE-ARGUMENT.md)
distinguishes checked propositions about emitted models from the additional
correspondence required to apply them to native Python. A failed proof, timeout
or unsupported construct is not automatically evidence of incompatibility.

The [supported-fragment inventory](https://github.com/astrio-labs/veripy/blob/main/docs/SUPPORTED-FRAGMENTS.md)
lists scoped arithmetic, loops, owned lists, string operations, modeled
exceptions and record snapshots. These are explicit boundaries, not blanket
support for arbitrary Python using those features. The repository documents
Dafny/Lean proof artifacts, native execution checks and case studies, but those
are upstream evidence, not a reproduction in Due or pytest-obligation. Install
from the linked repository when evaluating it: its README warns that the
`veripy` package on PyPI is unrelated.

Nagini's [research overview](https://www.pm.inf.ethz.ch/research/nagini.html)
describes modular verification of a typed Python subset, including concurrent
programs and specifications for I/O, progress and information flow. Its repository
documents an MCP server for file, method and snippet verification with structured
diagnostics and optional counterexamples. This makes it relevant both as a proof
backend candidate and as existing agent-facing infrastructure.

Apex's [limits](https://github.com/hyperionaidev/apex/blob/main/docs/LIMITS.md)
explicitly separate a Lean proof from translation correctness and from finite
native/model agreement. Its stated restrictions include substantial ordinary
Python behavior; assess the selected function before assuming it fits. Similarly,
CrossHair's [limitations](https://crosshair.readthedocs.io/en/latest/limitations.html)
matter for identity-sensitive code: symbolic proxies do not perfectly reproduce
all identity behavior. Proofside's documented sidecar contract language excludes
collections, floating point and general Python expressions; its relevance here
is the separation of proposed requirements from independently checked outcomes.

**Revised shortlist:** compare VeriPy and Nagini for implementation proofs, use
CrossHair where useful for fast counterexample search, and retain Quint for
protocol behavior and conformance histories. Consider Apex and Proofside for
specific workflow lessons or a demonstrated fit. None of this survey establishes
a ready-made Quint integration, universal Python support or production maturity
for our workloads.

**Next experiment and decision:** retain the conformance pilots as the immediate
adoption step. For a separate source-verification experiment, evaluate VeriPy and Nagini on
one small existing Python decision function and compare ordinary regressions plus Quint
conformance against that same baseline with source-level verification added.
Keep the source, contract and reviewed defect mutations fixed. Measure supported
semantics, obligations discharged, mutations rejected, annotation/proof effort,
regeneration behavior and CI time; report unresolved cases separately. Require
that a deliberate behavior change invalidates the relevant obligation without
weakening the contract. Use the result to choose an existing verifier, a narrow
integration, or no further investment. No Python verifier or proof-backed speedup
has been implemented or measured in this report.

### Compare the investments

| Approach | Agentic development | Production performance | CI implications |
| --- | --- | --- | --- |
| Python conformance layer | Concrete feedback on generated code | Enables safer changes | Adds focused behavioral checks |
| Contract-governed tasks | Precise implementation targets | Indirect | Earlier detection |
| Reusable contracts | Less repeated design and binding work | Indirect | Reusable scenarios and controls |
| Dependency-aware checks | Faster feedback | No direct effect | Potential savings after selection is validated |
| Contract-guided optimization | Constrained optimization tasks | Direct opportunity, measured per workload | Correctness and performance gates |
| Executable decision core | Less duplicated transition logic | Possible improvement or overhead | Compiler and host validation |
| Trace validation | Actual histories for debugging | Observation overhead | New regression inputs |
| Python source verification | Machine-checked local obligations and proof feedback | No inherent speedup; supports checked optimizations | Proof checking and generated-artifact freshness |

Prioritize the Python bridge, reusable contracts and agent workflow together.
Measure binding effort, defects detected, debugging time and CI cost in the two
pilots. Add dependency-aware checking and optimization support when justified.
A production compiler needs a recurring benefit large enough to support its
maintenance and correctness burden. Evaluate the LemmaScript-style Python
direction as a separate, narrowly scoped source-verification experiment using
existing tools first. These are compatible directions, not eight requirements for initial adoption.

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
