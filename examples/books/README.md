# Books: an indexing handoff that survives lost notifications

Create a book in Django, prepare its entry in an external search index, then
activate that entry and mark the book indexed in a second job. The browser lists
pending, indexed and stopped books; title/author searches use the external index.

This application ports Procrastinate's
[Django books demo](https://github.com/procrastinate-org/procrastinate/tree/8279a61789fdf8227d6f513ad15e4b26f1b78b49/procrastinate/demos/demo_django).
It preserves the create → index → mark-indexed journey, with an independently
written Due implementation. The upstream demo simulates indexing; this one uses
a real local SQLite index and adds failure proofs. Book submissions are immutable
in this example; editing, deletion and projection convergence are outside its scope.

## Compare directly with the original

The reference is Procrastinate commit
[`8279a61789fdf8227d6f513ad15e4b26f1b78b49`](https://github.com/procrastinate-org/procrastinate/tree/8279a61789fdf8227d6f513ad15e4b26f1b78b49/procrastinate/demos/demo_django).
Compare its [models](https://github.com/procrastinate-org/procrastinate/blob/8279a61789fdf8227d6f513ad15e4b26f1b78b49/procrastinate/demos/demo_django/demo/models.py),
[tasks](https://github.com/procrastinate-org/procrastinate/blob/8279a61789fdf8227d6f513ad15e4b26f1b78b49/procrastinate/demos/demo_django/demo/tasks.py)
and [views](https://github.com/procrastinate-org/procrastinate/blob/8279a61789fdf8227d6f513ad15e4b26f1b78b49/procrastinate/demos/demo_django/demo/views.py)
with our [models](models.py), [effects](effects.py), [services](services.py)
and [views](views.py). This is a port of that concrete application's journey,
not a comparison against a newly invented Procrastinate task.

| Same point in the journey | Original Procrastinate demo | This Due port |
| --- | --- | --- |
| Submit title and author | `CreateBookView.form_valid()` saves a `Book`, then calls `index_book.defer(book_id=...)` | `BookCreateView.form_valid()` calls `create_book()`, which saves the book and admits `IndexBookWork` together |
| Pending product state | `Book.indexed == False` | `Book.index_key == ""` and no failure flag |
| First task | `@app.task(queue="index") index_book`: sleep five seconds, check that the book exists | `IndexBook.perform_effect`: write a captured title/author entry to the external index |
| Handoff | `set_indexed.defer(book_id=...)` inside the first task's `transaction.atomic` | `IndexBook.finalize_success`: admit `PublishBookWork` inside fenced settlement |
| Second task | Blueprint-registered async `set_indexed`: `aupdate(indexed=True)` | `PublishBook`: activate the provider entry, then publish `Book.index_key` in local settlement |
| User-visible completion | List shows `Indexed: Yes` | List shows `Indexed`; title and author are unchanged |
| Worker setup | PostgreSQL plus Procrastinate worker; blueprint loaded through `PROCRASTINATE_ON_APP_READY` | PostgreSQL, Redis, Celery worker and Beat; both work types in `examples.registry` |

The direct comparison is **submit the same title/author, observe pending, run
both stages, observe indexed**. Keep this invariant when changing the port.
Changing a `.defer()` into `request_work()` alone does not explain the migration:
the successor becomes a typed obligation and product completion moves under Due's
settlement check.

Procrastinate already offers PostgreSQL-backed and transactional deferral. The
upstream first task explicitly wraps its handoff in a transaction; do not present
that as a missing library capability. Its create view saves then defers, whereas
this port uses a single admission transaction. That is a concrete application
change, not proof that Procrastinate cannot implement atomic admission.

### Additions that are not migration parity

The original waits five seconds to simulate indexing and has no external search
provider. The Due port removes that artificial delay and adds SQLite search,
provider replay keys, search UI, pagination, failure display and recovery proofs.
These additions make failure outcomes observable, but they are not work the
original demo performed. Neither elapsed time nor total line count is an honest
like-for-like queue comparison. Procrastinate's admin/blueprint demonstration is
also not reproduced by Due's status command and registry.

Books remain immutable submissions in both comparisons. This example does not
prove editing, stale-index convergence, original job-history import, or a complete
Procrastinate admin replacement. The Celery add-on lives in the separate
[document example](../celery_migration/README.md) and does not alter this lineage.

### Run upstream next to this port

Start the Due repository's PostgreSQL service and create a separate upstream
example database, once, from the Due checkout:

```bash
docker compose up -d --wait
docker compose exec -T postgres createdb -U due procrastinate
```

In a separate directory/terminal, check out the exact source (the installed
Procrastinate wheel does not include its demos):

```bash
git clone https://github.com/procrastinate-org/procrastinate.git procrastinate-origin
cd procrastinate-origin
git checkout 8279a61789fdf8227d6f513ad15e4b26f1b78b49
uv sync --locked --no-default-groups --extra django
# Supply libpq through the matching binary wheel when no system libpq is installed.
uv pip install 'psycopg[binary]==3.3.6'
export PYTHONPATH=.
export DJANGO_SETTINGS_MODULE=procrastinate.demos.demo_django.project.settings
export PGHOST=127.0.0.1 PGPORT=55439 PGUSER=due PGPASSWORD=due
uv run --no-sync python procrastinate/demos/demo_django/manage.py migrate
uv run --no-sync python procrastinate/demos/demo_django/manage.py runserver 127.0.0.1:8001
```

Use those same exports in the upstream worker terminal:

```bash
uv run --no-sync python procrastinate/demos/demo_django/manage.py procrastinate worker
```

Run Due on port 8000 using the instructions below, in terminals with Due's own
`DJANGO_SETTINGS_MODULE=examples.settings`. Do not point both applications at the
same database. The credentials/ports above are the local demo's compose defaults.

For each application, stop its worker, create **Kindred / Octavia Butler**, and
confirm the list shows pending. Start its worker (and Beat for Due); confirm it
becomes indexed with the same title/author. Upstream's create form is `/create/`;
Due's is `/books/new/`. Normalize the completion check to
`(title, author, indexed)` where Due's `indexed` is `bool(book.index_key)`.
Then exercise Due's extra search/recovery scenarios separately. Do not imply
that those scenarios have been run against upstream by passing Due's suite.

### Observed reference run

On 2026-10-11 the pinned upstream demo was run with PostgreSQL 17, Python 3.14.7
and its locked Django 6.1.1. A POST to its actual create view followed by its
`procrastinate worker --one-shot` produced
`("Kindred", "Octavia Butler", False) → ("Kindred", "Octavia Butler", True)`.
The Due browser/worker suites exercise the corresponding product journey and
its additional search outcomes. This is a functional comparison; upstream
failure injection and comparative performance were not measured.

## Run it

From the repository root:

```bash
uv sync --locked
docker compose up -d --wait
export DJANGO_SETTINGS_MODULE=examples.settings
uv run python -m django migrate
uv run python -m django runserver 127.0.0.1:8000
```

In two additional terminals, from the same checkout:

```bash
export DJANGO_SETTINGS_MODULE=examples.settings
uv run celery -A examples.celery_app:app worker --loglevel=INFO -Q due
```

```bash
export DJANGO_SETTINGS_MODULE=examples.settings
uv run celery -A examples.celery_app:app beat --loglevel=INFO
```

Open <http://127.0.0.1:8000>, add a book, and refresh until it shows **Indexed**.
Search by a substring of its title or author. There is no artificial processing
delay: to observe **Indexing pending**, create a book before starting the worker.

All examples share PostgreSQL, Redis, the Celery app and one recovery registry.
`DUE_DATABASE_URL`, `DUE_BROKER_URL` and `DUE_QUEUE` override the root defaults;
if changing the queue, change the worker's `-Q` too. Set `DUE_BOOK_INDEX` to change
the index path (default `/tmp/due-books/index.sqlite3`), identically in every
terminal. Web and workers must share that filesystem. SQLite is the demo search
provider, not the obligation store; PostgreSQL owns product and work state.

This is an unauthenticated local demonstration bound to loopback, not a hosted
catalog. The index is persistent across worker restarts but has no backup,
rebuild or retention workflow. Use a fresh local database/index together when
resetting the demo.

## Lose the first notification

Keep the worker and Beat running. From another terminal:

```bash
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django shell <<'PY'
from examples.books.services import create_book

book = create_book("Kindred", "Octavia Butler", notify_worker=False)
print(f"Book {book.pk} committed without a worker notification")
PY
```

The book and its obligation commit together, but no immediate notification is
sent. Beat's five-second recovery schedule finds it. Refresh the browser: the
book becomes indexed and searchable without another user request.

## Lose the handoff notification

For a reproducible manual handoff, stop Beat first and use a new submission:

```bash
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django shell <<'PY'
from due.django.testing import hold_nudges
from examples.books.effects import index_book
from examples.books.models import IndexBookWork, PublishBookWork
from examples.books.services import create_book

with hold_nudges():
    book = create_book("A Wizard of Earthsea", "Ursula K. Le Guin")
    index_book.complete(IndexBookWork.objects.get(owner=book))
print(f"Book {book.pk}: first stage settled, second stage {PublishBookWork.objects.get(owner=book).state}")
PY
```

The first stage is finished, but the book is still pending and hidden from
search. Restart Beat. The independent publication recovery route completes it.
The tests automate this history with a real Redis broker and prefork worker.

## Why the handoff is durable

1. `create_book` saves the book and admits indexing in one PostgreSQL transaction.
2. `IndexBook.perform_effect` writes an immutable admission/revision key to SQLite.
   Repeating the same write is safe.
3. `IndexBook.finalize_success` admits publication inside Due's fenced settlement
   transaction. The first stage cannot settle while its successor exists only in
   memory or an after-commit callback.
4. `PublishBook.perform_effect` idempotently activates the entry. Fenced settlement
   then publishes its key on the book. Search results also require that pointer,
   so an external activation without local settlement does not expose the book.

Neither stage promises exactly-once external execution. SQLite implements the
idempotent provider operations; Due keeps the obligation and fences local
settlement. Both stages expose exhausted retries through `indexing_failed`.
Inspect stopped work with `uv run python -m django due_status`, then explicitly
retry a book from the shell with `retry_indexing(book_id)` from `services.py`
after correcting the provider problem.

## Executable evidence

```bash
uv run pytest tests/examples/test_books.py tests/integration/test_books*.py -v
uv run pytest tests/conformance/test_book_contracts.py \
  -p due.django.conformance.reporting --due-work-require-assessed \
  --due-work-profile-report=book-profiles.json --durable-work-report=book-effects.json
```

- Browser submission, validation, CSRF and external-search outcomes call the real views.
- A successor-save interruption proves upstream settlement and successor admission roll back together.
- Generated replay proofs independently require exact indexed content and successful activation.
- Immutable-input proofs change product metadata, revisit indexing through operator retry
  and publication through settlement, then require unchanged input, revision and claim.
  Mutations that recapture metadata or replace the index key fail; no convergence debt remains.
- Generated contracts exercise each stage's recovery, ownership, replay, retry limits and product outcomes.
- Actual child processes exit after admission, before/after the index write,
  after the handoff, and before/after activation. Both production recovery routes
  must converge to one searchable book and one publication obligation.
- Redis/prefork journeys withhold either notification and check the real index
  and Django product state after recovery.
- Live journeys start separate web, worker and Beat processes. They submit the
  CSRF-protected form over HTTP, then withhold either stage's notification and
  require the real Beat schedule to make that book searchable over HTTP. No
  direct recovery call is made. Both Django CI configurations run these tests
  and retain process logs in the `obligation-profiles-*` artifacts.

These checks establish this application's contracts with its local provider.
They do not establish another search provider's idempotency or editing semantics.

See [migration findings and proof boundaries](../../docs/demo-comparisons.md) for
the cross-demo comparison and independently declared process outcomes.
