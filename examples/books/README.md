# Books: an indexing handoff that survives lost notifications

Create a book in Django, prepare its entry in an external search index, then
activate that entry and mark the book indexed in a second job. The browser lists
pending, indexed and stopped books; title/author searches use the external index.

This application is inspired by Procrastinate's
[Django books demo](https://github.com/procrastinate-org/procrastinate/tree/main/procrastinate/demos/demo_django).
It preserves the create → index → mark-indexed journey, with an independently
written Due implementation. The upstream demo simulates indexing; this one uses
a real local SQLite index and adds failure proofs. Book submissions are immutable
in this example; editing, deletion and projection convergence are outside its scope.

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
uv run pytest tests/examples/test_books.py tests/integration/test_books_worker.py -v
uv run pytest tests/conformance/test_book_contracts.py \
  -p due.django.conformance.reporting --due-work-require-assessed \
  --due-work-profile-report=book-profiles.json --durable-work-report=book-effects.json
```

- Browser submission, validation, CSRF and external-search outcomes call the real views.
- A successor-save interruption proves upstream settlement and successor admission roll back together.
- Generated contracts exercise each stage's recovery, ownership, replay, retry limits and product outcomes.
- Actual child processes exit after admission, before/after the index write,
  after the handoff, and before/after activation. Both production recovery routes
  must converge to one searchable book and one publication obligation.
- Redis/prefork journeys withhold either notification and check the real index
  and Django product state after recovery.

These checks establish this application's contracts with its local provider.
They do not establish another search provider's idempotency or editing semantics.
