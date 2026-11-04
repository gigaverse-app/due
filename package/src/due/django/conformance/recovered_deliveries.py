"""
Lose work's notifications, run the registered recovery task, and take the deliveries it publishes.

One owner for the step every recovery proof shares: age the rows, expire the
leases of workers the history loses, let production recovery do every
transition, and hold what it publishes instead of sending it to a broker.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from due.django.conformance.registrations import registration_of
from due.django.identities import work_id_from_wire
from due.django.models import DurableWorkState, UniverseDurableWork
from due.django.runtime.registry import DurableWorkRegistration
from due.django.runtime.tasks import recover_durable_work_task
from due.django.testing import expire_lease, make_due

#: One worker task a recovery tick published: its Celery arguments and options.
type HeldDelivery = tuple[tuple[Any, ...], dict[str, Any]]


@contextmanager
def held_deliveries(entry: DurableWorkRegistration) -> Iterator[list[HeldDelivery]]:
    """Hold every worker task published for ``entry`` instead of sending it to a broker."""
    sent: list[HeldDelivery] = []

    # Celery's apply_async keyword surface is the dynamic boundary here.
    def capture(*, args: tuple[Any, ...], **options: Any) -> None:
        if args[0] == entry.name:
            sent.append((args, options))

    with patch("due.django.runtime.tasks.run_durable_work_task.apply_async", capture):
        yield sent


def recovered_deliveries(works: Iterable[UniverseDurableWork], *, expire_claimed: bool) -> list[HeldDelivery]:
    """
    Lose every notification for ``works`` and return the deliveries registered recovery publishes for them.

    Each row is aged and its reservation's lease expired; ``expire_claimed``
    also expires an active claim's, for a history that loses its worker.
    Reaping a claim preserves the configured retry delay, so only that clock is
    advanced for one more tick. Production recovery performs every transition.
    """
    works = list(works)
    assert works, "recovery needs at least one owed work"
    entry = registration_of(type(works[0]))
    lost = (DurableWorkState.QUEUED, DurableWorkState.CLAIMED) if expire_claimed else (DurableWorkState.QUEUED,)
    for work in works:
        work.refresh_from_db()
        if work.state in lost:
            expire_lease(work)
        make_due(work)
    with held_deliveries(entry) as sent:
        recover_durable_work_task(entry.name)
        retrying = [work for work in works if _refreshed(work).state == DurableWorkState.RETRYABLE]
        for work in retrying:
            make_due(work)
        if retrying:
            recover_durable_work_task(entry.name)
    ids = {work.pk for work in works}
    return [(args, options) for args, options in sent if work_id_from_wire(entry.model, args[1]) in ids]


def recovered_delivery(work: UniverseDurableWork) -> HeldDelivery:
    """The one delivery recovery publishes for ``work`` after its notification and any worker were lost."""
    deliveries = recovered_deliveries([work], expire_claimed=True)
    assert len(deliveries) == 1, (
        f"{registration_of(type(work)).name}: configured recovery did not publish the producer's exact work"
    )
    return deliveries[0]


def _refreshed[W: UniverseDurableWork](work: W) -> W:
    work.refresh_from_db()
    return work
