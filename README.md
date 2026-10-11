# Due

**Durable obligations for Django.** An attempt can end. The obligation remains.

[![CI](https://github.com/gigaverse-app/due/actions/workflows/ci.yml/badge.svg)](https://github.com/gigaverse-app/due/actions/workflows/ci.yml)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue)](pyproject.toml)
[![Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Due records what your application owes in PostgreSQL, in the same transaction
as the product change. Celery runs the work; periodic recovery finds obligations
whose notifications were lost or whose workers died. Captured ownership tokens
and revisions prevent stale workers from publishing current database results.

This first alpha extracts Gigaverse's **work-table** primitive. It is Django-backed:
Django owns transactions, models and migrations. Native-table lifecycle composition
is outside this release. The API may change before 1.0.

## What it does

- Keeps accepted work discoverable after lost messages and worker death.
- Reserves deliveries, rejects duplicates, and fences stale settlement.
- Tracks changing desired state and movable/cancellable scheduled actions.
- Retains uncertain external operations until correlated evidence resolves them.
- Bounds retries and isolates poisoned recovery rows with backoff.
- Provides fault controls and `pytest-obligation` bindings for application tests.

A lease does **not** stop an already-sent external request. Replay safety,
idempotency, provider evidence and the definition of success remain application
contracts. Due does not promise exactly-once external execution.

## Install

Requires Python 3.14, PostgreSQL and a Django 6 application. Celery needs a broker
and a running worker plus a recurring recovery scheduler (normally Celery Beat).
The integration suite uses PostgreSQL 17 and Redis 7.

Until a PyPI release is published, install from GitHub:

```bash
uv add 'due[redis] @ git+https://github.com/gigaverse-app/due.git'
```

Add `"due.django"` to `INSTALLED_APPS`, then run your application's migrations.
Your own work models subclass `UniverseDurableWork`, declare captured inputs,
and include `durable_work_schema(...)` in their indexes and constraints.
`universe` is an unrestricted tenant key retained for extraction compatibility;
use an empty string for a single-tenant application.

A capability puts external I/O and local settlement in different methods:

```python
from due.django.public import DurableWorkPolicy, ReplaySafeEffect


class PublishDocument(ReplaySafeEffect[Document, PublicationWork, str]):
    def perform_effect(self, work: PublicationWork) -> str:
        return storage.put(
            key=work.provider_idempotency_key(),
            text=work.text,
        )

    def finalize_success(self, work: PublicationWork, result: str) -> None:
        Document.objects.filter(pk=work.owner_id).update(published_key=result)


publication = PublishDocument.for_owner(
    owner=Document,
    work=PublicationWork,
    policy=DurableWorkPolicy(),
)

with publication.atomic(document_id) as pending:
    pending.locked_owner.text = text
    pending.locked_owner.save(update_fields=["text"])
    pending.request_work(PublicationWork(text=text, universe="tenant-42"))
```

This sketch assumes your `Document`, `PublicationWork` and idempotent `storage`
implementation. The [complete runnable example](examples/documents/) includes
models, migrations, an actual filesystem effect, registration and Celery wiring.
Register every capability in **each process**; register once to generate the Beat
schedule and route both execution and recovery. See [integration](docs/integration.md).

## Run the example

```bash
git clone https://github.com/gigaverse-app/due.git
cd due
uv sync --locked
docker compose up -d
export DJANGO_SETTINGS_MODULE=examples.settings
uv run python -m django migrate
# Separate terminals, with the same settings:
uv run celery -A examples.celery_app:app worker --loglevel=INFO -Q due
uv run celery -A examples.celery_app:app beat --loglevel=INFO
```

Create a document from another terminal:

```bash
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django shell -c \
  'from examples.documents.models import Document; from examples.documents.effects import request_publication; document = Document.objects.create(); print(request_publication(document.pk, "Hello from Due").pk)'
```

The worker writes an immutable revision object under `/tmp/due-documents` and
publishes its key on the document row. Configure `DUE_DATABASE_URL`,
`DUE_BROKER_URL`, `DUE_OUTPUT_DIR` and `DUE_QUEUE` as needed. Compose credentials
are for local development only; both service ports bind to localhost.

## Develop and test

```bash
uv sync --locked
docker compose up -d
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
uv run pytest --due-work-profile-report=profiles.json
uv build
```

Tests use real PostgreSQL. The worker integration test also uses Redis and a
separate Celery prefork process. Set the connection URLs to point to dedicated
test services. `pytest-django` creates and drops its own test database.

Applications can install `due[testing]` for the `pytest-obligation` integration.
The generated suite proves recovery, ownership, retention, admission and retry
bounds for its bound example; it is not certification of arbitrary application
callbacks. See [verification](docs/verification.md) and [architecture](docs/architecture.md).

Licensed under [Apache-2.0](LICENSE). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).
