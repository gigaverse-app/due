"""Public fault injection for tests of durable-work applications.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from importlib import import_module
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING
from unittest.mock import patch

from due.django.capabilities.observed_operations import ObservedExternalOperationWork
from due.django.models import DurableWork, DurableWorkState, RecoveryDeferral
from due.django.runtime.coordination import database_now

if TYPE_CHECKING:
    from pytest_obligation import ObligationContract


@dataclass(frozen=True)
class HeldNudge:
    """
    An observed post-commit wake-up; no delivery claim has been acquired.

    ``work_name`` is the capability's registered name; ``work_id`` is the notified identity.
    """

    work_name: str
    work_id: int


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

    def capture(work_name: str, nudge: Callable[[int], object], work_id: int) -> None:
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
    with patch("due.django.runtime.coordination._read_database_clock", new=now):
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


def celery_worker_contract[WorkT: DurableWork](
    name: str,
    *,
    app: str,
    send: Callable[[], WorkT],
    effect: Callable[[WorkT], object],
    expected_effect: object,
    initial_effect: object = None,
    expected_state: DurableWorkState = DurableWorkState.SUCCEEDED,
    env: Mapping[str, str] | Callable[[], Mapping[str, str]] | None = None,
    worker_args: Sequence[str] = (),
    worker_setup: str | None = None,
    sigkill: bool = True,
    fixtures: tuple[str, ...] = (),
    timeout: float = 60.0,
    log: Path | None = None,
) -> ObligationContract:
    """
    Generated failure tests for one registered work through a real Celery worker, recovered by Due.

    Each generated history sends the work with the application's own producer,
    fails the worker at one of Celery's stages in the delivery that runs it (the
    pool child lost as the task starts, after the work ran or after Celery stored
    the task's result, also by ``SIGKILL``, and the task's ``on_success`` hook
    raising), then starts the worker again and lets Due recover the work: the
    abandoned delivery's lease runs out, and the recovery tick that the beat
    schedule publishes runs on the worker. Every history must leave the work in
    ``expected_state`` with the product effect ``expected_effect``, as a run
    with no failure does; nothing is declared.

    ``name`` is the work's registered name, ``app`` the Celery application as
    ``"module:attribute"``, and ``send`` the producer that records the work and
    returns its row. ``effect`` reads the product effect from that row, such as
    the published document it owes. The worker is a new process: ``env`` gives
    it what only the test knows, such as the test database's name, and it must
    consume the queue the test process publishes to. ``worker_setup``
    (``"module:function"``) runs in the worker once its tasks are loaded, to
    install the test doubles the work needs there, such as a fake provider or
    a filesystem storage. Each case commits for
    real, so the worker sees the rows and the wake-up follows the commit, and
    each history starts from an empty queue. Needs ``pytest-obligation[celery]``
    and Linux or macOS.
    """
    from pytest_obligation.integrations.celery_tasks import task_contract

    from due.django.runtime.names import RUN_TASK
    from due.django.runtime.tasks import recover_durable_work_task

    module, _, attribute = app.partition(":")
    application = getattr(import_module(module), attribute or "app")

    def sent() -> WorkT:
        # ARRANGE: no message another history left in the queue, then the application's own producer.
        application.control.purge()
        return send()

    def observe(work: WorkT) -> tuple[str, object]:
        # OBSERVE: the obligation's state, and the product effect it owes.
        work.refresh_from_db()
        return str(work.state), effect(work)

    def recover(work: WorkT) -> None:
        # Time passes: a delivery the worker abandoned runs out its lease, and the work is due again.
        work.refresh_from_db()
        if work.state in (DurableWorkState.QUEUED, DurableWorkState.CLAIMED):
            expire_lease(work)
        if work.state in (DurableWorkState.READY, DurableWorkState.QUEUED, DurableWorkState.RETRYABLE):
            make_due(work)
        # REAL PRODUCTION: the recovery tick the beat schedule publishes, run by the worker.
        recover_durable_work_task.delay(name)

    return task_contract(
        app,
        RUN_TASK,
        send=sent,
        effect=observe,
        initial_effect=(None, initial_effect),
        expected_effect=(str(expected_state), expected_effect),
        recovery=recover,
        recovered_by=f"Due's recovery of {name!r}",
        sigkill=sigkill,
        name=f"a worker runs {name!r} through Celery",
        fixtures=fixtures,
        transactional=True,
        env=env,
        worker_args=worker_args,
        worker_setup=worker_setup,
        timeout=timeout,
        log=log,
    )
