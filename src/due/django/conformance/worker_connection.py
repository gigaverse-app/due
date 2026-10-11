"""
A worker thread's own database connection: bounded while it runs, closed on every exit.

Tests that race production code on a second connection — competing deliveries,
a claim race, a repair beside retention — run each actor in its own thread. That
thread's connection needs two things the test's own connection does not:

* **A deadline on the server.** ``Future.result(timeout=...)`` or a thread join
  only bounds the caller's wait; neither cancels the worker's SQL, so a statement
  blocked on a lock would outlive the test. ``statement_timeout`` bounds every
  statement, an optional ``lock_timeout`` fails a lock wait with PostgreSQL's own
  error before the caller gives up, so a blocked worker reports why, and an
  optional ``idle_in_transaction_session_timeout`` ends a transaction the worker
  opened and then stalled inside in Python, which no statement bound covers.
* **A close on exit.** ``close_old_connections`` closes only connections past
  their ``CONN_MAX_AGE`` (which may be nonzero in an application), so a worker's connection
  would outlive the thread that owned it. ``connections.close_all()`` always
  closes this thread's connections.

The bounds are libpq startup parameters on this thread's copy of the connection
settings, so they hold from the first statement, survive a rolled-back
transaction, and cost nothing when the worker never touches the database. The
settings dict is shared by every thread's connection wrapper, so this thread's
wrapper gets a copy, and the scope puts the thread's previous settings back on
exit: a second scope on the same thread starts from them rather than stacking
its bounds on the first's. Enter the scope before the thread's first query: a
connection already open keeps the parameters it was opened with.

Worker threads only. On the test's own thread the copy would govern every later
connection the test opens, and ``close_all()`` would close the connection
holding the test's transaction, so the scope refuses it.

A worker that never returns keeps its connection until its thread ends, and one
stalled inside a transaction keeps its row locks with it. The thread that waited
for it ends those sessions with :func:`end_stalled_worker_sessions`, given the
wrappers each worker captured with :func:`worker_connection_wrapper`.
"""

import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from django.db import DEFAULT_DB_ALIAS, connection, connections
from django.db.backends.base.base import BaseDatabaseWrapper


# pytest-global: A worker thread's bounded, closed connection for race tests; a context manager, no setup on import.
@contextmanager
def database_worker_connection(
    *,
    statement_timeout_ms: int = 15_000,
    lock_timeout_ms: int | None = None,
    idle_in_transaction_timeout_ms: int | None = None,
) -> Iterator[None]:
    """Own a worker thread's bounded SQL and close its connections on every exit."""
    # Worker threads only: see the module docstring for what the test's own thread would lose.
    assert threading.current_thread() is not threading.main_thread(), (
        "database_worker_connection is for a worker thread; on the test's own thread its bounds would govern "
        "every later connection and close_all() would close the test's transaction"
    )
    assert statement_timeout_ms > 0, "worker SQL must have a finite deadline"
    for bound in (lock_timeout_ms, idle_in_transaction_timeout_ms):
        assert bound is None or bound > 0, "a lock or idle-transaction bound must be positive (0 disables it)"
    previous = connection.settings_dict
    if connection.vendor == "postgresql":
        named = {
            "lock_timeout": lock_timeout_ms,
            "statement_timeout": statement_timeout_ms,
            "idle_in_transaction_session_timeout": idle_in_transaction_timeout_ms,
        }
        bounds = " ".join(f"-c {name}={value}ms" for name, value in named.items() if value is not None)
        options = dict(previous.get("OPTIONS") or {})
        existing = options.get("options")
        options["options"] = f"{existing} {bounds}" if existing else bounds
        connection.settings_dict = {**previous, "OPTIONS": options}
    try:
        yield
    finally:
        connections.close_all()
        connection.settings_dict = previous


# pytest-global: Ends the sessions of worker threads that outlived their deadline; a pure function, no setup.
def end_stalled_worker_sessions(workers: Iterable[BaseDatabaseWrapper]) -> list[int]:
    """
    Terminate, from the test's own connection, the sessions of workers that never returned.

    ``workers`` are the stalled threads' own connection wrappers, captured in each
    thread. A worker that opened no connection holds nothing and is skipped. One
    stalled in Python inside a transaction is ``idle in transaction`` and keeps its
    row locks until its thread ends, so without this the test's teardown waits on
    them. Reading ``backend_pid`` asks libpq for the process id it already knows;
    it sends nothing on the worker's connection. Returns the terminated pids.
    """
    pids = [
        worker.connection.info.backend_pid
        for worker in workers
        if worker.vendor == "postgresql" and worker.connection is not None
    ]
    if pids:
        with connection.cursor() as cursor:
            for pid in pids:
                # Waits up to a second for the backend to exit, so its locks are gone on return.
                cursor.execute("SELECT pg_terminate_backend(%s, 1000)", [pid])
    return pids


# pytest-global: The calling thread's own default connection wrapper, for a stalled-session cleanup; no setup.
def worker_connection_wrapper() -> BaseDatabaseWrapper:
    """This thread's default connection wrapper; ``connection`` is a proxy that resolves per accessing thread."""
    return connections[DEFAULT_DB_ALIAS]
