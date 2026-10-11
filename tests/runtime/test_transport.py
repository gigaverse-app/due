"""
Commit notifications preserve the caller's transaction and thread, and share one
failure policy and one test seam.

Design and usage: docs/architecture.md.
"""

from threading import get_ident

import pytest
from asgiref.sync import async_to_sync
from django.db import transaction

from due.django.runtime import transport
from due.django.runtime.transport import after_commit, notify_after_commit
from due.django.testing import HeldNudge, hold_nudges

pytestmark = pytest.mark.django_db(transaction=True)


def test_bridge_waits_for_outermost_commit_on_the_transaction_thread() -> None:
    observed: list[int] = []
    with transaction.atomic():
        with transaction.atomic():
            async_to_sync(after_commit)(lambda: observed.append(get_ident()))
        assert observed == []
    assert observed == [get_ident()]


def test_bridge_drops_notification_on_rollback() -> None:
    observed: list[bool] = []
    with pytest.raises(RuntimeError, match="rollback"), transaction.atomic():
        async_to_sync(after_commit)(lambda: observed.append(True))
        raise RuntimeError("rollback")
    assert observed == []


def test_callback_failure_does_not_suppress_later_notifications() -> None:
    observed: list[bool] = []

    def broken() -> None:
        raise RuntimeError("broker unavailable")

    with transaction.atomic():
        async_to_sync(after_commit)(broken)
        async_to_sync(after_commit)(lambda: observed.append(True))
    assert observed == [True]


def test_without_transaction_notification_runs_immediately() -> None:
    observed: list[bool] = []
    async_to_sync(after_commit)(lambda: observed.append(True))
    assert observed == [True]


@pytest.mark.parametrize("fail", [False, True])
def test_effect_bridge_owns_and_closes_its_loop_thread_even_on_error(fail: bool) -> None:
    import asyncio
    import threading

    from due.django.runtime.transport import run_effect_coroutine

    threads: list[threading.Thread] = []
    loops: list[asyncio.AbstractEventLoop] = []
    caller = threading.current_thread()

    async def effect(value: str) -> str:
        threads.append(threading.current_thread())
        loops.append(asyncio.get_running_loop())
        if fail:
            raise RuntimeError("external operation failed")
        return value

    if fail:
        with pytest.raises(RuntimeError, match="external operation failed"):
            run_effect_coroutine(effect, "applied")
    else:
        assert run_effect_coroutine(effect, "applied") == "applied"
    assert threads[0] is not caller
    assert not threads[0].is_alive()
    assert loops[0].is_closed()


def test_notification_publishes_once_after_the_outermost_commit() -> None:
    published: list[int] = []
    with transaction.atomic():
        with transaction.atomic():
            notify_after_commit("example", published.append, 7)
        assert published == []
    assert published == [7]


def test_a_rolled_back_savepoint_discards_only_its_own_notification() -> None:
    published: list[int] = []
    with transaction.atomic():
        notify_after_commit("example", published.append, 1)
        with pytest.raises(RuntimeError, match="savepoint"), transaction.atomic():
            notify_after_commit("example", published.append, 2)
            raise RuntimeError("savepoint")
    assert published == [1]


def test_notification_without_a_transaction_publishes_immediately() -> None:
    published: list[int] = []
    notify_after_commit("example", published.append, 7)
    assert published == [7]


def test_publication_failure_is_observable_and_cannot_undo_the_commit(mocker) -> None:
    def broker_unavailable(identity: int) -> None:
        raise ConnectionError("broker unavailable")

    logged = mocker.patch.object(transport.logger, "exception")
    captured = mocker.patch.object(transport.sentry_sdk, "capture_exception")
    later: list[bool] = []

    with transaction.atomic():
        notify_after_commit("example", broker_unavailable, 7)
        transaction.on_commit(lambda: later.append(True))

    logged.assert_called_once_with(
        "Durable-work post-commit notification failed", extra={"effect": "example", "work_id": 7}
    )
    assert isinstance(captured.call_args.args[0], ConnectionError)
    assert later == [True]


def test_held_notifications_are_observed_after_commit_and_never_published() -> None:
    published: list[int] = []
    with hold_nudges() as held, transaction.atomic():
        notify_after_commit("example", published.append, 7)
        assert held == []
    assert held == [HeldNudge("example", 7)]
    assert published == []
