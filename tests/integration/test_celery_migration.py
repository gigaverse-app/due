"""The add-on baseline runs through Redis and a real prefork worker too."""

from functools import partial
from pathlib import Path

import pytest

from examples.celery_migration.tasks import publish_document_task, request_publication
from examples.documents.models import Document, PublicationWork
from tests.integration.worker import running_worker


@pytest.mark.django_db(transaction=True)
def test_plain_celery_publication_runs_in_real_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with running_worker(tmp_path, monkeypatch, imports=("examples.celery_migration.tasks",)) as worker:
        # Route this producer's real post-commit .delay() to the isolated queue.
        with monkeypatch.context() as routing:
            routing.setattr(
                publish_document_task, "apply_async", partial(publish_document_task.apply_async, queue=worker.queue)
            )
            document = Document.objects.create()
            request_publication(document.pk, "Celery baseline over Redis")

            def published() -> bool:
                document.refresh_from_db()
                return bool(document.published_key)

            worker.wait_until(published)
            assert (tmp_path / "objects" / document.published_key).read_text() == document.text
            assert not PublicationWork.objects.filter(owner=document).exists()
