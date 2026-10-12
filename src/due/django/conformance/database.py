"""Test-owned table for shared database proofs; never imports an adopter registry."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.test.utils import isolate_apps
from pytest_obligation.integrations.django.tables import model_tables
from pytest_obligation.integrations.django.transactions import transaction_in_progress as transaction_in_progress

from due.django.models import UniverseDurableWork, durable_work_schema

with isolate_apps("due.django"):

    class DatabaseWork(UniverseDurableWork):
        class Meta:
            app_label = "tests"
            indexes, constraints = durable_work_schema(name_prefix="db_proof")


@contextmanager
def database_work_table() -> Iterator[None]:
    with model_tables(DatabaseWork):
        yield
