---
name: due-integration
description: Build or change Django applications using Due, including Django Tasks adapters and native durable capabilities, with executable pytest-obligation application contracts.
---

# Implement the behavior and its contract together

Always build the application contract when integrating Due. Treat its generated
failure tests as part of the feature, including small examples and tutorials.
Do not finish at a passing happy path or defer the contract to optional hardening.

Read [integration](../../../package/docs/integration.md) and
[conformance](../../../package/docs/conformance.md). For a complete small adopter,
use [Acme Printworks](../../../examples/acme_printworks/README.md) and its
[contract bindings](../../../examples/acme_printworks/tests/contracts/).

1. State the customer-visible invariant and the provider's actual guarantees.
   Distinguish definite refusal from an unknown outcome. A timeout cannot prove
   that a purchase failed. Local ownership cannot cancel a remote write.
2. Implement the smallest suitable capability and register the production instance.
   Keep Django Tasks for ordinary replay-safe task bodies; use native capabilities
   when the product needs revision ownership, projection repair, observed effects,
   or independent compensation.
3. Configure `work_table_host(production_packages={...})`. For every native
   capability, bind `EffectExamples` or `ProviderSeam`/`seam_examples` and enroll it
   with `effect_contract_suite`. Bind actual application producers, provider calls,
   and independently observed product/provider results. Count real I/O. Do not
   implement recovery in bindings or substitute a fake capability.
4. Supply the mode-specific examples: stale inputs, terminal outcomes and rearm,
   ambiguous acceptance and receipts, compensation or other applicable families.
   Explain genuine non-applicability. Repair missing bindings instead of skipping
   failing generated tests or calling unassessed behavior verified.
5. Keep an independent production enrollment inventory and require full executed
   reports in CI. An empty, filtered, skipped, or deleted suite is not proof.
   Reuse Due's report checker in this repository. Run the real worker/scheduler
   path as well when changing wiring.
6. Verify representative observers with `EffectExamples.regressions` and the
   shared `EffectRegression` controls. Keep production commands and independent
   observations unchanged; Due generates the fault tests and checks collection.
   If a needed control lacks a declaration, extend Due's declaration and root
   controls rather than adding handwritten adopter proof calls or exemptions.
   For another library-owned proof, use `EffectExamples.extras` with `ExtraProof`;
   Due forwards it to the obligation generator and requires its collection.
   Use `RegressionControl` for shared profile proofs and
   `IndexedSelectionDependencies` for real Django dependency queries. Scope
   fixtures across the proof and keep state fresh across sensitivity runs.
   A matching setup or cleanup exception is infrastructure failure, never detection.
   Raise the testing dependency floor when adding an upstream API, verify the
   installed wheel with its testing extra, and release the provider first.

Before claiming coverage, inventory all producers for each owner: ordinary
commands, migration bridges, late retries, webhooks and narrow repairs. Use
obligation's `AdmissionOverlap` for cumulative scopes and
`SettlementInterleaving` at external observation boundaries where a competing
path can change terminal state. Declare each shared assertion through
`EffectExamples.extras` and `ExtraProof(run=LifecycleProof(...))`; the existing
collection guard requires those cases. Upgrade obligation to a version providing
these APIs first, or use an explicitly pinned upstream candidate for development.
Do not add another schedule runner or backend-only exemption. Include pending and completed first
requests, and observe every promised effect plus meaningful duplicate counts.
Immutable provider identity does not imply immutable effect scope. Verify the
original lost-effect behavior fails without changing the observation. Undeclared
pairs and checkpoints remain outside coverage despite passing ordinary cases.

The Django Tasks shared runtime is not enrolled as an application-native effect.
Always test each application's producer transaction, replay assumptions, and
customer outcome; add the native contract as soon as it gains a native capability.

The benefit is reusable, runnable failure histories and semantic assertions after
the application supplies meaningful bindings. Report actual counts, applicable
coverage and limitations. Never describe generated tests as proof of an unmodeled
provider guarantee, physical fulfillment, or universal correctness.


For multi-effect workflows, inventory each definite failure boundary, including
preparation before a once-only claim. Use obligation's PartialProgress through
LifecycleProof in extras: the shared proof requires the external fault to be
reached, observes the partial product result, removes the fault, then drives real
registered recovery. A manual second call to apply cannot establish recovery
ownership. Supply completed_effects call counts when successful groups must be
checkpointed; the proof rejects repeated successful work and runs recovery once
more after completion. Bind separate cases for preparation and ambiguous
publication. Do not turn an ambiguous publish into a definite retry to satisfy
this proof. Use RegressionControl to verify dropped remaining work and repeated
completed groups are detected. Inventory every effect observed by the scenario;
groups absent from its data remain outside coverage.
