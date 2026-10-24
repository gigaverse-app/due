"""Exercise the documented task with real PostgreSQL, Redis, worker death and Beat.

The outer pytest test supplies its database and a unique queue. This child owns
all worker/scheduler lifetimes through the existing process helper.
"""

import os
import sys
from pathlib import Path

import django
from django.core.management import call_command
from django.db import transaction
from django.tasks import TaskResult, TaskResultStatus
from django.tasks.exceptions import TaskResultDoesNotExist

from integration_tests.integration.processes import Process, running_process


def wait_for_result(worker: Process, result: TaskResult) -> None:
    def finished() -> bool:
        result.refresh()
        return result.is_finished

    worker.wait_until(finished, timeout=60)
    assert result.status == TaskResultStatus.SUCCESSFUL


def check_rollback() -> None:
    from due.django.conformance import hold_nudges
    from examples.hello_due.tasks import greet

    with hold_nudges() as notifications:
        try:
            with transaction.atomic():
                result = greet.enqueue("Rolled back")
                raise ValueError("Reject the request")
        except ValueError:
            pass
    assert not notifications
    try:
        greet.get_result(result.id)
    except TaskResultDoesNotExist:
        return
    raise AssertionError("Rolled-back invocation survived")


def check_workers(directory: Path) -> None:
    from due.django.conformance import hold_nudges
    from examples.hello_due.tasks import greet

    env = dict(os.environ)
    worker_command = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "examples.hello_due.celery:app",
        "worker",
        "--pool=solo",
        "--concurrency=1",
        "--loglevel=INFO",
        "--without-gossip",
        "--without-mingle",
        "-Q",
        env["DUE_QUEUE"],
    ]
    # Enqueue before any worker exists; retrieve through a fresh result handle.
    queued_id = greet.enqueue("Ada").id
    queued = greet.get_result(queued_id)
    assert queued.status == TaskResultStatus.READY

    with running_process(directory, "first-worker", worker_command, env) as worker:
        wait_for_result(worker, queued)
        assert queued.return_value == "Hello, Ada!"
        assert "Hello, Ada!" in worker.log.read_text()

        interrupted = greet.enqueue("Grace", seconds=10)
        marker = f"Starting greeting for Grace in process {worker.handle.pid}"
        worker.wait_until(lambda: marker in worker.log.read_text())
        interrupted.refresh()
        assert interrupted.status == TaskResultStatus.RUNNING
        assert "Hello, Grace!" not in worker.log.read_text()
        worker.handle.kill()
        assert worker.handle.wait(timeout=5) < 0

    with hold_nudges() as dropped:
        lost = greet.enqueue("Linus")
    assert len(dropped) == 1
    assert lost.status == TaskResultStatus.READY

    beat_command = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "examples.hello_due.celery:app",
        "beat",
        "--loglevel=INFO",
        "--schedule",
        str(directory / "beat-schedule"),
    ]
    # Real elapsed lease expiry and recurring recovery. No row edits or explicit recover().
    with (
        running_process(directory, "restarted-worker", worker_command, env) as restarted,
        running_process(directory, "beat", beat_command, env) as beat,
    ):
        wait_for_result(restarted, interrupted)
        wait_for_result(restarted, lost)
        assert interrupted.return_value == "Hello, Grace!"
        assert lost.return_value == "Hello, Linus!"
        log = restarted.log.read_text()
        assert "Starting greeting for Grace" in log
        assert "Hello, Grace!" in log
        assert "Hello, Linus!" in log
        assert beat.handle.poll() is None, beat.log.read_text()

    assert greet.get_result(queued_id).return_value == "Hello, Ada!"


def main(directory: Path) -> None:
    django.setup()
    from examples.hello_due.celery import app

    call_command("migrate", verbosity=0, interactive=False)
    call_command("due_check")
    try:
        check_rollback()
        check_workers(directory)
        print("tutorial journey passed: queued, retrieved, rolled back, killed, restarted, recovered")
    finally:
        with app.connection_or_acquire() as broker:
            broker.default_channel.queue_delete(queue=os.environ["DUE_QUEUE"])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
