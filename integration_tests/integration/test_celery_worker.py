"""Real Redis delivery, Celery prefork worker, PostgreSQL and filesystem effect."""

from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection

from due.django.models import DurableWorkState
from due.django.runtime.registry import registration_named
from due.django.testing import hold_nudges
from examples.documents.effects import request_publication
from examples.documents.models import Document
from integration_tests.integration.worker import running_worker

pytestmark = pytest.mark.django_db(transaction=True)


def test_worker_recovers_lost_notification_and_publishes_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = Document.objects.create()
    with hold_nudges():
        work = request_publication(document.pk, "Persisted even when the first message is lost.")
    assert work.state == DurableWorkState.READY

    def published() -> bool:
        document.refresh_from_db()
        return bool(document.published_key)

    with running_worker(tmp_path, monkeypatch) as worker:
        worker.recover("publish_document")
        worker.wait_until(published)
        assert (tmp_path / "objects" / document.published_key).read_text() == document.text
        work.refresh_from_db()
        assert work.state == DurableWorkState.SUCCEEDED
        assert work.applied_revision == work.desired_revision
        assert work.attempt_count == 1
        assert not connection.in_atomic_block
        assert settings.CELERY_BEAT_SCHEDULE
        assert registration_named("publish_document").capability.model is type(work)
