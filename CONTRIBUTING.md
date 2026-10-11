# Contributing

Use Python 3.14 and `uv sync --locked`. Start isolated PostgreSQL and Redis with
`docker compose up -d`; run the checks listed in the README. Keep the lockfile
current with `uv lock` when dependencies change.

Keep product policy out of the coordinator. Fix reliability bugs with a failing
real-boundary regression first. Preserve owner/work lock order, token/revision
fencing, and committed evidence before changing recovery behavior. Never turn
unknown external outcomes into replay permission based only on elapsed time.

Prefer the existing pytest-obligation bindings and fault controls. Observe real
product outcomes as well as work state. Run Ruff, Pyrefly, tests, migration checks
and `uv build` before submitting a pull request. Contributions use Apache-2.0.

To build a distribution: `uv build`. PyPI publication is a separate maintainer
release action; this repository has no automatic upload on push or pull request.
