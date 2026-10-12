# Package-only subtree layout

The package directory owns editable source, reusable controls and library maintenance documentation. Runnable applications and their full integration proofs remain in the public repository. No guarantee or existing proof is removed.

- [ ] Build and install the package in isolation; no parent repository or demos required.
- [ ] Run package controls from a standalone subtree export, without example modules.
- [ ] Run the complete OSS suite, preserving existing controls and strict findings.
- [ ] Validate docs ownership and links from both the full checkout and package-only export.
- [ ] Exercise standard subtree split/import/export on changed package source and docs; reject an incorrect baseline.
- [ ] Verify paired BE CI applies its committed candidate packages before executing OSS controls.
- [ ] Validate publishing workflow, release build paths, lint and types.
