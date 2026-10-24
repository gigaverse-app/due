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

The Django Tasks shared runtime is not enrolled as an application-native effect.
Always test each application's producer transaction, replay assumptions, and
customer outcome; add the native contract as soon as it gains a native capability.

The benefit is reusable, runnable failure histories and semantic assertions after
the application supplies meaningful bindings. Report actual counts, applicable
coverage and limitations. Never describe generated tests as proof of an unmodeled
provider guarantee, physical fulfillment, or universal correctness.
