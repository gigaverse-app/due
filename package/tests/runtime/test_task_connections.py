"""Real Celery tracing must release worker sessions and preserve eager callers."""

from uuid import uuid4

import pytest
from celery import current_app
from celery.app.trace import build_tracer
from django.db import connection, transaction

from due.django.runtime.tasks import DatabaseTask

pytestmark = pytest.mark.django_db(transaction=True)


class ReadDatabase(DatabaseTask):
    name = "connection-boundary-control"

    def run(self, fail: bool = False) -> int:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 17")
            result = cursor.fetchone()[0]
        if fail:
            raise RuntimeError("application failure")
        return result


@pytest.mark.parametrize("fail", [False, True])
def test_worker_returns_connection_after_success_or_failure(fail):
    task = ReadDatabase()
    task.bind(current_app)
    trace = build_tracer(task.name, task, app=current_app)
    result = trace(str(uuid4()), [fail], {}, {"is_eager": False})
    assert (result.info is not None) is fail
    if not fail:
        assert result.retval == 17
    assert connection.connection is None


def test_eager_task_keeps_callers_transaction_and_connection():
    task = ReadDatabase()
    task.bind(current_app)
    trace = build_tracer(task.name, task, app=current_app, eager=True)
    with transaction.atomic():
        connection.ensure_connection()
        borrowed = connection.connection
        result = trace(str(uuid4()), [], {}, {"is_eager": True})
        assert result.retval == 17
        assert connection.connection is borrowed
        assert connection.in_atomic_block
        with connection.cursor() as cursor:
            cursor.execute("SELECT 19")
            assert cursor.fetchone()[0] == 19
