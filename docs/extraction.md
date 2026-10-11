# Initial extraction

Due carries Gigaverse's obligation-oriented execution model into a standalone,
public Python library. PostgreSQL owns durable intent, claims, revisions and
evidence; queue deliveries are replaceable execution opportunities.

## Required stack

- Python 3.14 and uv, with a committed lockfile.
- Pydantic for validated application input and evidence contracts.
- PostgreSQL for durable state and transaction boundaries.
- Celery for workers and periodic recovery.
- Ruff and Pyrefly for formatting, linting and type checking.
- pytest-obligation for generated behavioral contracts.

## Extraction checks

- Preserve atomic admission with the application's transaction.
- Recover lost delivery and expired execution ownership.
- Fence stale workers from database settlement.
- Preserve unknown external outcomes without unsafe blind replay.
- Bound retries and isolate poisoned recovery identities.
- Document capabilities, provider responsibilities and limitations.
- Exercise the standalone distribution against real PostgreSQL and Celery.

Implementation and verification are in progress.
