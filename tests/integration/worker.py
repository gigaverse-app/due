"""A real isolated Celery worker shared by runnable-application journeys."""

import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import connection

from due.django.runtime import tasks
from examples.celery_app import app
from tests.integration.processes import Process, running_process


@dataclass(frozen=True)
class Worker:
    process: Process
    queue: str
    env: dict[str, str]

    def recover(self, name: str) -> None:
        tasks.recover_durable_work_task.apply_async(args=(name,), queue=self.queue)

    def wait_until(self, ready: Callable[[], bool]) -> None:
        self.process.wait_until(ready)


@contextmanager
def running_worker(directory: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Worker]:
    base = os.environ.get("DUE_DATABASE_URL", "postgresql://due:due@127.0.0.1:55439/due")
    database_url = base.rsplit("/", 1)[0] + "/" + str(connection.settings_dict["NAME"])
    queue = "due-test-" + uuid4().hex
    app.set_current()
    app.set_default()
    monkeypatch.setenv("DUE_OUTPUT_DIR", str(directory / "objects"))
    monkeypatch.setenv("DUE_BOOK_INDEX", str(directory / "index.sqlite3"))
    worker_env = {
        **os.environ,
        "DUE_DATABASE_URL": database_url,
        "DUE_QUEUE": queue,
        "DJANGO_SETTINGS_MODULE": "examples.settings",
    }
    try:
        with running_process(
            directory,
            "worker",
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "examples.celery_app:app",
                "worker",
                "--pool=prefork",
                "--concurrency=1",
                "--loglevel=INFO",
                "--without-gossip",
                "--without-mingle",
                "-Q",
                queue,
                "--hostname",
                queue + "@%h",
            ],
            worker_env,
        ) as process:
            yield Worker(process, queue, worker_env)
    finally:
        with app.connection_or_acquire() as broker:
            broker.default_channel.queue_delete(queue=queue)
