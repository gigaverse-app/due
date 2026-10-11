# Initial extraction verification

- [x] Port work-table regressions onto isolated PostgreSQL tables and standalone settings.
- [x] Bind generated recovery, ownership, retention, admission and retry-limit proofs to Due.
- [x] Drop the initial notification, then recover through Redis and a real Celery prefork process.
- [x] Observe the actual published file and fenced product state in the runnable example.
- [x] Retain uncertainty and recover exact receipts without blind replay.
- [x] Verify poisoned recovery, rollback, duplicate deliveries, tenant context and stale-token rejection.
- [x] Run Ruff, Pyrefly, migration consistency and package builds.
- [x] Verify an installed wheel outside the source checkout (Django 6.1.2, migrations and imports).
- [x] Run GitHub CI on the extraction implementation commit (`a1e30be`).
- [x] Disable production pruning temporarily; confirm the generated retention positive control fails, then passes after restoration.
