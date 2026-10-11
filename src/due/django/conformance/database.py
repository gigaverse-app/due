"""Test-owned table for shared database proofs; never imports an adopter registry."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection, connections
from django.test.utils import isolate_apps

from due.django.models import UniverseDurableWork, durable_work_schema

with isolate_apps("due.django"):

    class DatabaseWork(UniverseDurableWork):
        class Meta:
            app_label = "tests"
            indexes, constraints = durable_work_schema(name_prefix="db_proof")


@contextmanager
def database_work_table() -> Iterator[None]:
    with connection.schema_editor() as editor:
        editor.create_model(DatabaseWork)
    try:
        yield
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(DatabaseWork)


@contextmanager
def transaction_in_progress() -> Iterator[None]:
    """
    Hold an xid older than everything the body writes.

    Snapshot xmin is server-wide, so this keeps the body's dead row versions
    recently dead: an index built over them gets ``pg_index.indcheckxmin`` and
    the planner ignores it. Locally nothing else holds one, so plan-sensitive
    tests that only fail in CI reproduce with this.
    """
    other = connections.create_connection(connection.alias)
    try:
        other.set_autocommit(False)
        with other.cursor() as cursor:
            cursor.execute("SELECT txid_current()")
        yield
    finally:
        other.close()
