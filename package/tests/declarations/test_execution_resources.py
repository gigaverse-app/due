"""Nested reuse keeps factory-owned cleanup and never crosses execution owners."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context

import pytest
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext

from due.django.public import ExecutionResource


@pytest.mark.parametrize("fail", [False, True])
def test_nested_reuse_acquires_once_and_cleans_up_once(fail):
    events = []

    @contextmanager
    def open_resource(key):
        events.append(("open", key))
        try:
            yield object()
        finally:
            events.append(("close", key))

    resource = ExecutionResource(open_resource)
    try:
        with resource.acquire("a") as outer:
            for _ in range(3):
                with resource.acquire("a") as inner:
                    assert inner is outer
            with resource.acquire("b") as other:
                assert other is not outer
            if fail:
                raise RuntimeError("body failed")
    except RuntimeError as error:
        assert fail and str(error) == "body failed"
    assert events == [("open", "a"), ("open", "b"), ("close", "b"), ("close", "a")]


def test_copied_context_cannot_reuse_another_thread_or_expired_resource():
    @contextmanager
    def factory(key):
        yield object()

    resource = ExecutionResource(factory)

    def borrow():
        with resource.acquire("a") as value:
            return value

    with resource.acquire("a") as outer:
        copied = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(copied.run, borrow).result(timeout=5) is not outer
    assert copied.run(borrow) is not outer


@pytest.mark.asyncio
async def test_child_task_opens_its_own_resource():
    @contextmanager
    def factory(key):
        yield object()

    resource = ExecutionResource(factory)

    async def borrow():
        with resource.acquire("a") as value:
            return value

    with resource.acquire("a") as outer:
        assert await asyncio.create_task(borrow()) is not outer


def test_failed_validation_does_not_borrow_or_leak_the_resource():
    live = []
    allowed = True

    @contextmanager
    def factory(key):
        live.append(key)
        try:
            yield key
        finally:
            live.remove(key)

    def validate(value):
        if not allowed:
            raise RuntimeError("session replaced")

    resource = ExecutionResource(factory, validate)
    with resource.acquire("a"):
        allowed = False
        with pytest.raises(RuntimeError, match="session replaced"), resource.acquire("a"):
            pytest.fail("invalid resource was borrowed")
        assert live == ["a"]
    assert live == []
    with pytest.raises(RuntimeError, match="session replaced"), resource.acquire("a"):
        pytest.fail("invalid resource was acquired")
    assert live == []


@pytest.mark.django_db(transaction=True)
def test_postgres_lock_reuse_preserves_exclusion_until_the_outer_scope_exits():
    key = 83147590

    @contextmanager
    def lock(identity):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [identity])
        try:
            yield identity
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [identity])

    def available():
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
                obtained = cursor.fetchone()[0]
                if obtained:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [key])
                return obtained
        finally:
            connections.close_all()

    resource = ExecutionResource(lock)
    with ThreadPoolExecutor(max_workers=1) as observer:
        with CaptureQueriesContext(connection) as queries:
            with resource.acquire(key):
                for _ in range(9):
                    with resource.acquire(key):
                        assert not observer.submit(available).result(timeout=5)
                assert not observer.submit(available).result(timeout=5)
        assert observer.submit(available).result(timeout=5)
    assert len(queries) == 2
