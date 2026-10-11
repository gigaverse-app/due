# Developer experience and work-table parity

- [ ] Audit runtime extraction and port missing operator inspection.
- [ ] Restore registry, dependent-wake, mirror and recovery regression coverage.
- [ ] Explain the failure model, capability choices and runnable demo in the README.
- [ ] Add locked pre-commit checks and separate CI lanes, including wheel installation.
- [ ] Run PostgreSQL/Redis tests, static checks, package smoke and GitHub CI.

Reference: gigaverse-app/pytest-obligation's failure-first README, locked uv
installs, independent CI jobs and installed-wheel validation. That repository
has no pre-commit configuration; Due will supply its own using its locked tools.
