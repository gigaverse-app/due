"""Commit notifications and synchronous execution of asynchronous effect bodies.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

import logging
from collections.abc import Awaitable, Callable
from functools import partial

import sentry_sdk
from asgiref.sync import AsyncSingleThreadContext, async_to_sync, sync_to_async
from django.db import transaction

from due.django.identities import WorkId

logger = logging.getLogger(__name__)


async def after_commit(callback: Callable[[], object]) -> None:
    """Notify after the outermost commit; callback failure cannot undo that commit."""
    await sync_to_async(transaction.on_commit)(callback, robust=True)


def notify_after_commit[KeyT: WorkId](work_name: str, publish: Callable[[KeyT], object], identity: KeyT) -> None:
    """
    Publish a wake-up for a committed obligation after the outermost commit.

    Rollback, including of the enclosing savepoint, discards it; with no
    transaction it runs now. Registration binds ``publish_notification`` so
    ``testing.hold_nudges()`` observes and drops it.
    """
    transaction.on_commit(partial(publish_notification, work_name, publish, identity), robust=True)


def publish_notification[KeyT: WorkId](work_name: str, publish: Callable[[KeyT], object], identity: KeyT) -> None:
    """
    Publish one committed notification; failure is observable, never raised.

    The obligation is already committed, so a broker error cannot change the
    caller's outcome. Recovery owns eventual delivery; this only shortens the
    common-case latency.
    """
    try:
        publish(identity)
    except Exception as error:
        sentry_sdk.capture_exception(error)
        logger.exception(
            "Durable-work post-commit notification failed",
            extra={"effect": work_name, "work_id": identity},
        )


def run_effect_coroutine[**Params, ResultT](
    operation: Callable[Params, Awaitable[ResultT]], /, *args: Params.args, **kwargs: Params.kwargs
) -> ResultT:
    """Run an async effect now from a synchronous worker; always close its executor."""
    with AsyncSingleThreadContext():
        return async_to_sync(operation)(*args, **kwargs)
