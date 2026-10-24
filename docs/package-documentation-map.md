# Documentation ownership

The package subtree includes the docs needed to change and operate the library. Repository guides and application showcases stay with their full checkout. Each document has one owner.

Readers start with [the background-task tutorial](../package/docs/quickstart.md), then
follow [the capability chapters](../package/docs/tutorial.md) and use
[the API and file map](../package/docs/api-guide.md) for lookup. These live in
`package/docs/` so the entry points travel with the library. Each chapter owns one
capability's teaching sequence and links to its prepared example. The longer
[Printworks story](acme-printworks-tutorial.md) stays with its repository example.

| Previous path | Canonical path | Owner |
| --- | --- | --- |
| `docs/architecture.md` | `package/docs/architecture.md` | Library API, guarantees, adoption or maintenance |
| `docs/backend-adoption.md` | `package/docs/backend-adoption.md` | Library API, guarantees, adoption or maintenance |
| `docs/capabilities.md` | `package/docs/capabilities.md` | Library API, guarantees, adoption or maintenance |
| `docs/completion-leases.md` | `package/docs/completion-leases.md` | Library API, guarantees, adoption or maintenance |
| `docs/conformance-api.md` | `package/docs/conformance-api.md` | Library API, guarantees, adoption or maintenance |
| `docs/conformance.md` | `package/docs/conformance.md` | Library API, guarantees, adoption or maintenance |
| `docs/cutover-audit.md` | `package/docs/cutover-audit.md` | Library API, guarantees, adoption or maintenance |
| `docs/integration.md` | `package/docs/integration.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations.md` | `package/docs/migrations.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/arq.md` | `package/docs/migrations/arq.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/celery.md` | `package/docs/migrations/celery.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/chancy.md` | `package/docs/migrations/chancy.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/dbos.md` | `package/docs/migrations/dbos.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/django-q2.md` | `package/docs/migrations/django-q2.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/django-rq.md` | `package/docs/migrations/django-rq.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/django-tasks-db.md` | `package/docs/migrations/django-tasks-db.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/django-tasks-rq.md` | `package/docs/migrations/django-tasks-rq.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/django-tasks.md` | `package/docs/migrations/django-tasks.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/dramatiq.md` | `package/docs/migrations/dramatiq.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/huey.md` | `package/docs/migrations/huey.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/pgmq.md` | `package/docs/migrations/pgmq.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/pgqueuer.md` | `package/docs/migrations/pgqueuer.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/procrastinate.md` | `package/docs/migrations/procrastinate.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/rq.md` | `package/docs/migrations/rq.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/taskiq.md` | `package/docs/migrations/taskiq.md` | Library API, guarantees, adoption or maintenance |
| `docs/migrations/temporal.md` | `package/docs/migrations/temporal.md` | Library API, guarantees, adoption or maintenance |
| `docs/operations.md` | `package/docs/operations.md` | Library API, guarantees, adoption or maintenance |
| `docs/verification.md` | `package/docs/verification.md` | Library API, guarantees, adoption or maintenance |

Root `README.md`, contributor/release guides, project plans and demo-specific documentation remain outside the package. `package/README.md` is the library entry point; it links to the public showcase instead of duplicating it.

The package-local [development guide](../package/docs/development.md) owns reusable test commands and the source/split/export model. Full application integration proofs stay in `integration_tests/`; their fixtures are colocated there when they depend on demo applications.
