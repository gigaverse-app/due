"""Baseline controls/gaps; the existing document suite owns Due's failure proofs."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from django.db import transaction

from due.django.conformance.background import BackgroundWork, accounted_background_work, frozen_scheduler_clock
from due.django.testing import WorkerDied, die_after
from examples.celery_migration.tasks import publish_document_task, request_publication
from examples.documents import storage
from examples.documents.models import Document

pytestmark = pytest.mark.django_db(transaction=True)
TEXT = "First publication"
NEW_TEXT = "Replacement publication"


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[BackgroundWork]:
    monkeypatch.setenv("DUE_OUTPUT_DIR", str(tmp_path))
    with frozen_scheduler_clock() as clock, accounted_background_work(monkeypatch, clock) as driver:
        yield driver


def assert_published(document: Document, directory: Path, expected: str) -> None:
    document.refresh_from_db()
    assert document.published_key, "Accepted publication must eventually publish its key"
    assert (directory / document.published_key).read_text() == expected


def test_celery_baseline_publishes_the_same_business_result(broker: BackgroundWork, tmp_path: Path) -> None:
    document = Document.objects.create()
    request_publication(document.pk, TEXT)
    assert_published(document, tmp_path, TEXT)
    assert len(broker.executed) == 1


def test_rolled_back_submission_never_dispatches(broker: BackgroundWork) -> None:
    document = Document.objects.create(text=TEXT)
    with transaction.atomic():
        request_publication(document.pk, NEW_TEXT)
        assert not broker.dispatches
        transaction.set_rollback(True)
    document.refresh_from_db()
    assert document.text == TEXT
    assert not broker.dispatches


def test_redelivery_after_provider_write_is_safe(broker: BackgroundWork, tmp_path: Path) -> None:
    broker.hold()
    document = Document.objects.create()
    request_publication(document.pk, TEXT)
    with patch.object(storage, "write_revision", die_after(storage.write_revision)), pytest.raises(WorkerDied):
        broker.release()
    assert len(list(tmp_path.iterdir())) == 1
    assert Document.objects.get(pk=document.pk).published_key == ""
    assert len(broker.executed) == 1
    # Explicit transport redelivery. This proves application replay safety,
    # not Redis acknowledgement or Celery's prefork-child loss behavior.
    delivery = broker.executed[0]
    delivery.task.apply_async(args=delivery.args, kwargs=delivery.kwargs)
    assert_published(document, tmp_path, TEXT)
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="Baseline has no durable lost-message recovery")
def test_accepted_publication_survives_lost_delivery(broker: BackgroundWork, tmp_path: Path) -> None:
    broker.hold()
    document = Document.objects.create()
    request_publication(document.pk, TEXT)
    if len(broker.drop_held(publish_document_task)) != 1:
        raise RuntimeError("Fault control failed: the intended delivery must actually be lost")
    broker.release()
    broker.advance(600)
    assert_published(document, tmp_path, TEXT)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="Baseline has no stale local completion fence")
def test_newer_publication_wins_when_old_worker_finishes_last(broker: BackgroundWork, tmp_path: Path) -> None:
    document = Document.objects.create()
    original = storage.write_revision
    interrupted = False

    def write_then_publish_newer(text: str, operation_key: str) -> str:
        nonlocal interrupted
        key = original(text, operation_key)
        if not interrupted:
            interrupted = True
            request_publication(document.pk, NEW_TEXT)
        return key

    with patch.object(storage, "write_revision", write_then_publish_newer):
        request_publication(document.pk, TEXT)
    if not interrupted or len(broker.executed) != 2:
        raise RuntimeError("Fault control failed: both old and new workers must finish")
    assert_published(document, tmp_path, NEW_TEXT)
