# Package-only subtree layout

The package directory owns editable source, reusable controls and library maintenance documentation. Runnable applications and their full integration proofs remain in the public repository. No guarantee or existing proof is removed.

- [x] Build and install the package in isolation; no parent repository or demos required.
- [x] Run package controls from a standalone subtree export, without example modules.
- [x] Run the complete OSS suite, preserving existing controls and strict findings.
- [x] Validate docs ownership and links from both the full checkout and package-only export.
- [x] Exercise standard subtree split/import/export on changed package source and docs; reject an incorrect baseline.
- [ ] Complete the real paired BE run on #5360; committed-candidate negative controls already pass.
- [x] Validate publishing workflow, release build paths, lint and types.

Evidence: standalone archive **814 passed**; full OSS applications **796 passed, 2 existing xfailed**, with primitive/document/deadline report gates passing. All 536 original test functions remain. Package-only wheel/sdist builds and strict metadata checks pass; no demo/app directories appear in the sdist. Both lint and type checks pass.

BE transport validation is owned by [#5360](https://github.com/gigaverse-app/gigaverse-backend/pull/5360): 26 controls cover real split/import/export/reimport, deletions, provenance mismatches, committed private imports and broken candidate packages. Package Markdown links were checked after moves; see the documentation ownership map.
