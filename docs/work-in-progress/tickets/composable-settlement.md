# Composable settlement, one engine

Baseline: Due aeb2922 (#158), Backend mail bf533c35b with static eligibility,
native mail 1ea8f661c. Existing measured incremental successful-chunk cost is
21 SQL / 8 DML / 4 commits for Due and 11 / 5 / 3 for native.

The provider owns acceptance; the attempt owns retained evidence; the work row
owns product progress and replay permission. Queue messages remain hints.
Preserve all existing public declarations and callback semantics by default.

| Step / PR | Value if later work stops | Proof | Non-claim |
|---|---|---|---|
| Stage bulk progress through one internal transition; pin settlement boundaries | Both bulk modes share one validated transition; generated regression control detects lost progress and retry race bindings verify fault entry | Real PostgreSQL hook/rollback checks, ordinary executor/recovery, both receipt layouts, generated sensitivity controls | No speedup or transaction change |
| Opt-in composition of progress and completion writes | Existing engine can persist declared compatible updates together | Same generated contracts plus legacy/opt-in visibility and failure matrix | No implicit opt-in or receipt/settlement commit fusion |
| Mail adoption | Real consumer exercises the composed path | Mail's generated obligations and unchanged native comparison harness | No rollout flag activation or native parity claim without evidence |

Existing finalizers may read newly saved progress while work is still claimed.
That is a write barrier. Only an explicit new declaration can permit an
in-memory progress hook before the combined save; existing callers keep their
barrier. Hooks, dependent wakes and recovery must not silently move across it.

Receipt commit remains independent of product settlement. A savepoint cannot
replace that boundary for process death. Claim/entry fusion and batched
settlement are later research, not part of this initial implementation.

Test plan:
- [x] Live/recovery settlement visibility tests for both receipt layouts.
- [x] A failure in progress or success hooks rolls back product changes while
      independently visible applied evidence survives and recovery never resends.
- [x] Generated sensitivity controls detect missing progress persistence.
- [x] Both bulk modes use the same staging logic and protected-field validation.
- [ ] Benchmark each exact step and native with identical shortlink behavior.
- [ ] Final CI includes generated contracts, fresh package export and live workers.
