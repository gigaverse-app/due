# Due and Procrastinate: the same immutable-book outcomes

This is an independently written comparison against the Procrastinate version
pinned in `uv.lock` (currently 3.10.0). It uses the same `Book` product model and
actual SQLite search provider as the [Due books example](../books/README.md).
Procrastinate is a development dependency, not a dependency of the Due wheel.

The Procrastinate implementation has transactional product/job admission through
its Django connector, captured task arguments, a stable provider key, sequential
locks, bounded retries, and the documented periodic stalled-job recovery task.
It is not the upstream tutorial with its simulated indexing delay.

```bash
uv sync --locked
docker compose up -d --wait
DJANGO_SETTINGS_MODULE=examples.comparison.settings uv run python -m django migrate
DJANGO_SETTINGS_MODULE=examples.comparison.settings uv run python -m django procrastinate worker -q due-comparison
```

From another terminal:

```bash
DJANGO_SETTINGS_MODULE=examples.comparison.settings uv run python -m django shell <<'PY'
from examples.comparison.tasks import create_book
book = create_book('Kindred', 'Octavia Butler')
print(book.pk)
PY
uv run pytest tests/comparison -v
```

The ordinary books browser also reads these books: launch it with
`DJANGO_SETTINGS_MODULE=examples.comparison.settings uv run python -m django runserver`.
Its existing create form still admits Due work; use the command above for the
Procrastinate producer.

## Shared failure matrix

| History | Due | Procrastinate |
| --- | --- | --- |
| Normal indexing and publication | One searchable book | One searchable book |
| Caller rolls admission back | No product or queued work | No product or queued work |
| Producer dies after admission | Registered recovery completes | Persisted queue completes |
| Worker dies after external index commit | Safe replay and completion | Stalled-job retry and completion |
| Worker dies after external activation | Safe replay and completion | Stalled-job retry and completion |

The tests call real producers, abruptly exit actual child processes and read the
same provider/product outcome. Due uses its existing child executor and generated
recovery driver; Procrastinate uses its actual one-shot worker and recovery task.
Both execute production coordination code. Due's driver advances lease/retry
clocks; Procrastinate's test shortens heartbeat detection only after the child is
known dead. These are correctness histories, not comparable latency measurements.

## What the comparison establishes

Both implementations must pass all eleven cases in both Django CI configurations.
Crash recovery and atomic enqueueing are shared capabilities. Due additionally
provides a model for fenced product settlement and application-bound generated
contracts; its other demos exercise revision repair and uncertain operations.
Those richer semantics are not compared against Procrastinate here.

Application code can be inspected directly:

- Shared product/provider: `examples/books/models.py` (`Book`) and `search.py`.
- Due-specific work models, effects and commands: `examples/books/models.py`
  (`BookWork`, `IndexBookWork`, `PublishBookWork`), `effects.py`, `services.py`.
- Procrastinate-specific tasks, admission and stalled recovery: `tasks.py` here.

Due requires two application work tables and declarations; Procrastinate uses
its shared job tables. Due also includes product exhaustion status and an
operator retry command that this narrowly scoped Procrastinate implementation
does not implement. Raw line counts therefore do not establish equivalent
feature cost. The shared index contract bindings reduce Due's repeated test code.

The Due deployment uses PostgreSQL, Redis, a Celery worker and Beat; this
Procrastinate deployment needs PostgreSQL and a worker, with periodic work run
by that worker. Existing Celery deployments can reuse their broker and scheduler.
No throughput, database CPU, memory, network cost or production failure rate has
been measured. Do not interpret test runtime or the total suite size as such a
measurement.
