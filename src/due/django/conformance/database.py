"""Test-owned table for shared database proofs; never imports an adopter registry."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection
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
