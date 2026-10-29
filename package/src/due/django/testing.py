"""Public fault injection for tests of durable-work applications.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from threading import Lock
from unittest.mock import patch

from django.db.models import QuerySet, Value

from due.django.capabilities.observed_operations import ObservedExternalOperationWork
from due.django.identities import WorkId
from due.django.models import DurableWork, DurableWorkState, RecoveryDeferral
from due.django.runtime.coordination import ClockReading, database_now


@dataclass(frozen=True)
class HeldNudge:
    """
    An observed post-commit wake-up; no delivery claim has been acquired.

    ``work_name`` is the capability's registered name; ``work_id`` is the notified identity.
    """

    work_name: str
    work_id: WorkId


@contextmanager
def hold_nudges() -> Iterator[list[HeldNudge]]:
    """
    Capture and drop fast wake-ups during this scope, including nested commits.

    Enclose both registration and the outermost commit: callbacks bind the
    shared publication seam when registered and run after that commit.
    Explicit enqueue/recover calls still reserve and publish normally; patch the
    broker boundary to simulate lost *reserved* delivery. The patch is process
    scoped, so use separate test processes for concurrent independent scenarios.
    """
    held: list[HeldNudge] = []

    def capture(work_name: str, nudge: Callable[[WorkId], object], work_id: WorkId) -> None:
        held.append(HeldNudge(work_name, work_id))

    with patch("due.django.runtime.transport.publish_notification", new=capture):
        yield held


def make_due(work: DurableWork) -> None:
    """Age availability only; execution and recovery still perform all transitions."""
    assert work.pk is not None, "record the obligation through its producer first"
    type(work).objects.filter(pk=work.pk).update(available_at=database_now() - timedelta(seconds=1))


def end_recovery_holds(work_model: type[DurableWork] | None = None) -> None:
    """
    Let every identity a recovery phase deferred be retried on the next tick, as if its backoff elapsed.

    Holds are advisory: the identity stays owed in its own table, and the next
    tick performs every transition itself. Pass a work model to end only its holds.
    """
    holds = RecoveryDeferral.objects.all()
    if work_model is not None:
        holds = holds.filter(effect=work_model._meta.label_lower)
    holds.update(retry_at=database_now() - timedelta(seconds=1))


def make_observation_due(work: ObservedExternalOperationWork) -> None:
    """
    Schedule the next observation now on the coordination clock; state and leases are untouched.

    ``next_observation_at`` defaults to PostgreSQL's ``Now()``. Inside a frozen
    or travelled test clock that real timestamp is ahead of ``database_now()``,
    so work that production just made UNKNOWN is not yet observable.
    """
    assert work.pk is not None, "make the obligation uncertain through its producer first"
    type(work).objects.filter(pk=work.pk).update(next_observation_at=database_now())


def expire_lease(work: DurableWork) -> None:
    """Simulate a dead reserved/active worker without changing ownership or state."""
    assert work.pk is not None, "claim the persisted obligation first"
    changed = (
        type(work)
        .objects.filter(pk=work.pk, state__in=(DurableWorkState.QUEUED, DurableWorkState.CLAIMED))
        .update(lease_expires_at=database_now() - timedelta(seconds=1))
    )
    assert changed == 1, "only an existing reserved or active claim has a lease to expire"


@contextmanager
def use_database_clock(now: Callable[[], datetime]) -> Iterator[None]:
    """
    Use one test clock for all library reads, including bound function defaults.

    Share the same clock with Django/provider fixtures when simulating elapsed
    time. This replaces only the clock transport; locks, eligibility, leases and
    settlement still execute against PostgreSQL. No work rows are aged or
    transitioned by this control. The patch is process-scoped, like hold_nudges;
    independent concurrent scenarios need separate test processes.
    """

    def read_clock(exists: QuerySet | None = None) -> ClockReading:
        return now(), None if exists is None else exists.exists()

    with (
        patch("due.django.runtime.coordination._read_database_clock", new=read_clock),
        patch("due.django.runtime.coordination._database_statement_clock", new=lambda: Value(now())),
    ):
        yield


class WorkerDied(BaseException):
    """A test worker stopped; normal exception classification must not run."""


def die_after[**P, ResultT](operation: Callable[P, ResultT]) -> Callable[P, ResultT]:
    """
    Stop the worker after the injected operation first returns normally.

    Inject the returned callable at the provider transport seam. Subsequent
    calls delegate normally, so real recovery can run. An exception propagates
    unchanged and does not consume the fault. Arrange and observe the fake
    applying the write when proving death-after-apply: an HTTP error response
    also returns normally and does not prove acceptance. This synchronous
    control never marks evidence applied or runs a settlement callback.
    """
    armed = True
    lock = Lock()

    @wraps(operation)
    def call(*args: P.args, **kwargs: P.kwargs) -> ResultT:
        nonlocal armed
        result = operation(*args, **kwargs)
        with lock:
            if armed:
                armed = False
                raise WorkerDied("provider operation returned before the worker stopped")
        return result

    return call
