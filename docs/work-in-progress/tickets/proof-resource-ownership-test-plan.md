# Proof resource ownership

One binding-entry contract must preserve a proof failure through adopter cleanup.
Use the existing obligation scope owner for Due and handoff histories; preserve
plain-value factories and context-manager cleanup semantics.

- [x] Delivery verdict through the real primitive: broken observation remains a
  failure with provider, application or delivery cleanup that suppresses assertions.
  All three fail before the fix; the unsuppressed negative and four successful
  delivery controls pass before it. All eight pass after it.
- [x] Existing provider-seam proofs: real admission, recovery and settlement;
  only external provider calls are replaced. Sixteen cases pass.
- [x] Broader Due run: 1,363 pass and four existing expected failures; one source-inspection
  case was invalidated by a test-file edit during execution. Rerun the complete
  provider-seam file on fixed source and the final integrated suite before merge.
- [x] Obligation core: 948 passed, one optional-dependency skip; Django: 366 passed,
  one existing expected failure. Includes generated suppression and handoff cleanup controls.
- [x] Ruff and types pass for the changed obligation modules and the full Due project.
- [ ] Paired backend/public CI after integration with the cutoff port.

This is a correctness improvement as well as consolidation: the former Due
factory scope allowed adopter cleanup to erase an assertion. The shared owner
keeps the exception identity and cleanup behavior while preventing a false pass.
