"""Real Redis delivery, Celery prefork worker, PostgreSQL and filesystem effect."""

import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import connection

from due.django.models import DurableWorkState
from due.django.runtime import tasks
from due.django.runtime.registry import registration_named
from due.django.testing import hold_nudges
from examples.celery_app import app
from examples.documents.effects import request_publication
from examples.documents.models import Document

pytestmark = pytest.mark.django_db(transaction=True)


def test_worker_recovers_lost_notification_and_publishes_document(tmp_path: Path) -> None:
    document = Document.objects.create()
    with hold_nudges():
        work = request_publication(document.pk, "Persisted even when the first message is lost.")
    assert work.state == DurableWorkState.READY

    # Only a test-database connection string crosses into the isolated worker.
    database_url = os.environ.get("DUE_DATABASE_URL", "postgresql://due:due@127.0.0.1:55439/due")
    database_url = database_url.rsplit("/", 1)[0] + "/" + str(connection.settings_dict["NAME"])
    queue = "due-test-" + uuid4().hex
    app.set_current()
    app.set_default()
    worker_env = {
        **os.environ,
        "DUE_DATABASE_URL": database_url,
        "DUE_OUTPUT_DIR": str(tmp_path / "objects"),
        "DUE_QUEUE": queue,
        "DJANGO_SETTINGS_MODULE": "examples.settings",
    }
    log_path = tmp_path / "worker.log"
    with log_path.open("w+") as log:
        worker = subprocess.Popen(
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
            env=worker_env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            # Invoke the real recovery task through Redis. The worker then
            # discovers the persisted row and publishes its normal execution.
            tasks.recover_durable_work_task.apply_async(args=("publish_document",), queue=queue)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                document.refresh_from_db()
                if document.published_key:
                    break
                assert worker.poll() is None, log_path.read_text()
                time.sleep(0.1)
            assert document.published_key, log_path.read_text()
            assert (tmp_path / "objects" / document.published_key).read_text() == document.text
            work.refresh_from_db()
            assert work.state == DurableWorkState.SUCCEEDED
            assert work.applied_revision == work.desired_revision
            assert work.attempt_count == 1
            assert not connection.in_atomic_block
            assert settings.CELERY_BEAT_SCHEDULE
            assert registration_named("publish_document").capability.model is type(work)
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
            with app.connection_or_acquire() as broker:
                broker.default_channel.queue_delete(queue=queue)
