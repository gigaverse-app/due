"""
Generated failure tests for a registered work through a real Celery worker, recovered by Due.

A module of its own, apart from :mod:`due.django.testing`: it starts real workers, so whatever imports it
depends on the application's whole task graph, and the fault controls most tests import must not.
See docs/architecture.md for Due's guarantees and integration boundaries.
"""

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid7

from due.django.models import DurableWork, DurableWorkState
from due.django.testing import expire_lease, make_due

if TYPE_CHECKING:
    from pytest_obligation import ObligationContract

    from due.django.runtime.registry import DurableWorkRegistration


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
    queue: str | None = None,
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
    it what only the test knows, such as the test database's name.
    ``worker_setup`` (``"module:function"``) runs in the worker once its tasks
    are loaded, to install the test doubles the work needs there, such as a
    fake provider or a filesystem storage.

    The work runs on a queue of its own (``queue``, by default a new name), so
    the worker takes only this test's messages even when other tests share the
    broker: the registrations are routed there while ``send`` runs and in the
    worker, the recovery tick is published there, and each history starts with
    it empty. Each case commits for real, so the worker sees the rows and the
    wake-up follows the commit. Needs ``pytest-obligation[celery]`` and Linux
    or macOS.
    """
    from pytest_obligation.integrations.celery_tasks import task_contract

    from due.django.runtime.names import RUN_TASK
    from due.django.runtime.registry import installed
    from due.django.runtime.tasks import recover_durable_work_task

    module, _, attribute = app.partition(":")
    application = getattr(import_module(module), attribute or "app")
    own_queue = queue or f"due-celery-{uuid7().hex}"

    def sent() -> WorkT:
        # ARRANGE: no message another history left on the work's own queue.
        with application.connection_for_write() as connection:
            connection.default_channel.queue_purge(own_queue)
        # REAL PRODUCTION: the application's own producer, its wake-up routed to the work's own queue.
        with installed.installed(routed_to(own_queue, installed.get())):
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
        recover_durable_work_task.apply_async(args=(name,), queue=own_queue)

    def worker_environment() -> dict[str, str]:
        given = env() if callable(env) else env
        return {**(given or {}), _TEST_QUEUE: own_queue, _TEST_SETUP: worker_setup or ""}

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
        env=worker_environment,
        worker_args=(*worker_args, "-Q", own_queue),
        worker_setup="due.django.celery_testing:route_worker_to_test_queue",
        timeout=timeout,
        log=log,
    )


#: The queue a worker started by ``celery_worker_contract`` takes its work from, and the adopter's own setup.
_TEST_QUEUE, _TEST_SETUP = "DUE_CELERY_TEST_QUEUE", "DUE_CELERY_TEST_SETUP"


def routed_to(queue: str, registrations: Mapping[str, DurableWorkRegistration]) -> dict[str, DurableWorkRegistration]:
    """Every registration routed to ``queue``, for one test's worker; production routes stay untouched."""
    return {name: replace(registration, queue=queue) for name, registration in registrations.items()}


def route_worker_to_test_queue() -> None:
    """
    ARRANGE, in a worker ``celery_worker_contract`` starts: the work it publishes stays on the test's queue.

    Runs once the worker has loaded its tasks; routes the registrations in each
    pool child, where the work runs, then calls the adopter's own setup.
    """
    from celery import signals

    from due.django.runtime.registry import installed

    queue = os.environ[_TEST_QUEUE]

    def route(**_kwargs: object) -> None:
        installed.install(routed_to(queue, installed.get()))

    signals.worker_process_init.connect(route, weak=False)
    setup = os.environ.get(_TEST_SETUP, "")
    if setup:
        module, _, function = setup.partition(":")
        getattr(import_module(module), function)()
