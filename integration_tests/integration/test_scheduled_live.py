"""Lost notifications survive a worker restart; Beat rediscovers due publications."""

import sys
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from due.django.capabilities.scheduled import CancellationResult
from due.django.testing import hold_nudges
from examples.scheduled.models import Publication
from examples.scheduled.services import cancel_publication, create_publication
from integration_tests.integration.processes import running_process
from integration_tests.integration.worker import running_worker

pytestmark = pytest.mark.django_db(transaction=True)


def test_restart_and_beat_recover_deadline_but_not_cancelled_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with running_worker(tmp_path, monkeypatch) as first:
        first.wait_until(lambda: "ready." in first.process.log.read_text())
        with hold_nudges():
            deadline = timezone.now() + timedelta(seconds=3)
            publication = create_publication("Published after restart", deadline)
            cancelled = create_publication("Withdrawn edition", deadline)
            assert cancel_publication(cancelled.pk) == CancellationResult.CANCELLED
        assert not (tmp_path / "objects").exists()
    # No manually invoked recovery or completion: a fresh worker and actual
    # Beat must discover the persisted deadline after the old worker has exited.
    with (
        running_worker(tmp_path, monkeypatch) as worker,
        running_process(
            tmp_path,
            "beat",
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "examples.celery_app:app",
                "beat",
                "--loglevel=INFO",
                "--schedule",
                str(tmp_path / "beat-schedule"),
            ],
            worker.env,
        ) as beat,
    ):

        def published() -> bool:
            publication.refresh_from_db()
            if timezone.now() < deadline:
                assert publication.status == Publication.Status.SCHEDULED
                assert not (tmp_path / "objects").exists()
            return publication.status == Publication.Status.PUBLISHED

        beat.wait_until(published)
        assert worker.process.handle.poll() is None, worker.process.log.read_text()
        assert (tmp_path / "objects" / publication.published_key).read_text() == publication.text
        assert len(list((tmp_path / "objects").iterdir())) == 1
        cancelled.refresh_from_db()
        assert cancelled.status == Publication.Status.CANCELLED
        assert not cancelled.published_key


def test_near_deadline_publishes_with_real_worker_without_beat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from due.django.conformance.registrations import durable_work_installed
    from due.django.runtime.registry import registration_for
    from examples.scheduled.models import PublicationDeadline

    with running_worker(tmp_path, monkeypatch) as worker:
        worker.wait_until(lambda: "ready." in worker.process.log.read_text())
        route = replace(registration_for(PublicationDeadline), queue=worker.queue)
        with durable_work_installed(route):
            deadline = timezone.now() + timedelta(seconds=2)
            publication = create_publication("Timer without Beat", deadline)

        def published() -> bool:
            publication.refresh_from_db()
            if timezone.now() < deadline:
                assert publication.status == Publication.Status.SCHEDULED
            return publication.status == Publication.Status.PUBLISHED

        worker.wait_until(published)
        assert (tmp_path / "objects" / publication.published_key).read_text() == publication.text
