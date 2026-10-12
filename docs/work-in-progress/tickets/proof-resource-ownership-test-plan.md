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
- [x] Broader Due run on fixed, integrated source: **1,364 passed and four
  existing expected failures** before merging the scheduled-publication feature.
  Final combined run: **1,461 passed, four existing expected failures**.
- [x] Obligation core: 948 passed, one optional-dependency skip; Django: 366 passed,
  one existing expected failure. Includes generated suppression and handoff cleanup controls.
- [x] Ruff and types pass for the changed obligation modules and the full Due project.
- [x] Paired backend adopter validation: **2,112 passed, 70 existing expected
  failures and six collection exclusions**, including the 19-test policy directory. One rendition history was intentionally consolidated on main.
- Final source CI is enforced by [Due #22](https://github.com/gigaverse-app/due/pull/22/checks)
  and [obligation #57](https://github.com/gigaverse-app/pytest-obligation/pull/57/checks).

This is a correctness improvement as well as consolidation: the former Due
factory scope allowed adopter cleanup to erase an assertion. The shared owner
keeps the exception identity and cleanup behavior while preventing a false pass.
