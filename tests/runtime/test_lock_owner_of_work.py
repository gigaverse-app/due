"""
Locking a stored work row's owner by id takes the owner's row lock and not the work row's.

Reap, receipt settlement and non-application reconciliation know only a work
id. They lock its owner in one statement whose sub-select names the owner, then
lock the work row themselves: owner before work, as every writer of both does.
PostgreSQL applies ``FOR UPDATE`` only to the rows of the statement's own FROM
list, so the sub-select must leave the work row unlocked; a second connection
observes which rows are held.

Design: docs/architecture.md.
"""

from collections.abc import Iterator

import pytest
from django.db import DEFAULT_DB_ALIAS, OperationalError, connection, connections, transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models import Model
from django.test.utils import CaptureQueriesContext

from due.django.runtime.coordination import lock_owner_of_work
from tests.harness.primitive import PrimitiveOwner, PrimitiveWork, record
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


@pytest.fixture(name="other_connection")
def other_connection_fixture() -> Iterator[BaseDatabaseWrapper]:
    other = connections.create_connection(DEFAULT_DB_ALIAS)
    try:
        yield other
    finally:
        other.close()


def _held_elsewhere(other: BaseDatabaseWrapper, model: type[Model], pk: int | None) -> bool:
    """Whether another transaction holds this row's lock: a non-waiting lock attempt from a second connection."""
    table = connection.ops.quote_name(model._meta.db_table)
    with other.cursor() as cursor:
        try:
            # Test-owned identifiers only; the value is a bound parameter.
            # nosemgrep: python.django.security.audit.raw-query.raw-query
            cursor.execute(f"SELECT 1 FROM {table} WHERE id = %s FOR UPDATE NOWAIT", [pk])
        except OperationalError:
            return True
    return False


def test_locks_the_owner_and_leaves_the_work_row_to_the_caller(other_connection: BaseDatabaseWrapper):
    work = record()
    with transaction.atomic(), CaptureQueriesContext(connection) as captured:
        lock_owner_of_work(PrimitiveWork, work.pk)

        assert _held_elsewhere(other_connection, PrimitiveOwner, work.owner_id)
        assert not _held_elsewhere(other_connection, PrimitiveWork, work.pk)
    # One statement: no read of the work row to learn its owner first.
    statements = [query["sql"] for query in captured.captured_queries if query["sql"] not in ("BEGIN", "COMMIT")]
    assert len(statements) == 1, statements


@pytest.mark.parametrize("gone", ["owner", "work"])
def test_a_missing_owner_or_row_locks_nothing(gone: str, other_connection: BaseDatabaseWrapper):
    work = record()
    owner_id = work.owner_id
    if gone == "owner":
        PrimitiveOwner.objects.filter(pk=owner_id).delete()  # SET_NULL: the work row outlives it
    else:
        PrimitiveWork.objects.filter(pk=work.pk).delete()
    with transaction.atomic():
        lock_owner_of_work(PrimitiveWork, work.pk)

        assert not _held_elsewhere(other_connection, PrimitiveOwner, owner_id)
        assert not _held_elsewhere(other_connection, PrimitiveWork, work.pk)
