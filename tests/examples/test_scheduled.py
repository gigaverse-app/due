# ruff: noqa: F811
from datetime import timedelta
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from due.django.capabilities.scheduled import CancellationResult, ScheduleResult
from due.django.conformance.background import BackgroundWork, background_work, scheduler_clock  # noqa: F401, F811
from due.django.testing import hold_nudges
from examples.documents import storage
from examples.scheduled.models import Publication
from examples.scheduled.services import cancel_publication, create_publication, reschedule_publication

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def file_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUE_OUTPUT_DIR", str(tmp_path))


def assert_published(publication: Publication, root: Path) -> None:
    publication.refresh_from_db()
    assert publication.status == Publication.Status.PUBLISHED
    assert (root / publication.published_key).read_text() == publication.text
    assert len(list(root.iterdir())) == 1


def test_lost_wakeup_waits_for_deadline_then_recovers(background_work: BackgroundWork, tmp_path: Path) -> None:
    with hold_nudges():
        publication = create_publication("Tomorrow's news", timezone.now() + timedelta(seconds=60))
    background_work.advance(59)
    background_work.run_recovery("publish_at_deadline")
    assert list(tmp_path.iterdir()) == []
    background_work.advance(1)
    background_work.run_recovery("publish_at_deadline")
    assert_published(publication, tmp_path)


@pytest.mark.parametrize("replacement_seconds", [10, 120])
def test_reschedule_ignores_old_delivery_and_obeys_replacement(
    background_work: BackgroundWork, tmp_path: Path, replacement_seconds: int
) -> None:
    now = timezone.now()
    background_work.hold()
    publication = create_publication("Moved edition", now + timedelta(seconds=60))
    # Capture a real worker message from recovery once the original is due.
    background_work.advance(60)
    background_work.run_recovery("publish_at_deadline")
    old = background_work.work_deliveries("publish_at_deadline")[-1]
    at = timezone.now() + timedelta(seconds=replacement_seconds)
    assert reschedule_publication(publication.pk, at) == ScheduleResult.SCHEDULED
    background_work.deliver(old)
    assert list(tmp_path.iterdir()) == []
    background_work.release()
    background_work.advance(replacement_seconds - 1)
    background_work.run_recovery("publish_at_deadline")
    assert list(tmp_path.iterdir()) == []
    background_work.advance(1)
    background_work.run_recovery("publish_at_deadline")
    assert_published(publication, tmp_path)


def test_move_deadline_earlier(background_work: BackgroundWork, tmp_path: Path) -> None:
    now = timezone.now()
    publication = create_publication("Early edition", now + timedelta(seconds=120))
    assert reschedule_publication(publication.pk, now + timedelta(seconds=10)) == ScheduleResult.SCHEDULED
    background_work.advance(10)
    background_work.run_recovery("publish_at_deadline")
    assert_published(publication, tmp_path)


def test_cancelled_publication_survives_old_delivery_and_recovery(
    background_work: BackgroundWork, tmp_path: Path
) -> None:
    background_work.hold()
    publication = create_publication("Withdrawn", timezone.now())
    old = background_work.work_deliveries("publish_at_deadline")[-1]
    assert cancel_publication(publication.pk) == CancellationResult.CANCELLED
    background_work.deliver(old)
    background_work.release()
    background_work.advance(3600)
    background_work.run_recovery("publish_at_deadline")
    publication.refresh_from_db()
    assert publication.status == Publication.Status.CANCELLED
    assert list(tmp_path.iterdir()) == []


def test_change_after_provider_entry_does_not_lie_about_product(
    background_work: BackgroundWork, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with hold_nudges():
        publication = create_publication("Already printing", timezone.now())
    original_deadline = publication.publish_at
    original = storage.write_revision

    def write(text: str, operation_key: str) -> str:
        assert cancel_publication(publication.pk) == CancellationResult.ALREADY_STARTED
        assert (
            reschedule_publication(publication.pk, timezone.now() + timedelta(days=1)) == ScheduleResult.ALREADY_STARTED
        )
        publication.refresh_from_db()
        assert publication.publish_at == original_deadline
        assert publication.status == Publication.Status.SCHEDULED
        return original(text, operation_key)

    monkeypatch.setattr(storage, "write_revision", write)
    background_work.run_recovery("publish_at_deadline")
    assert_published(publication, tmp_path)


def test_browser_and_cli_use_shared_commands(client: Client, background_work: BackgroundWork, tmp_path: Path) -> None:
    at = timezone.now() + timedelta(hours=1)
    response = client.post(
        "/scheduled/", {"text": "Browser draft", "at": at.isoformat(), "submission_key": str(uuid4())}
    )
    assert response.status_code == 302
    publication = Publication.objects.get()
    detail = f"/scheduled/{publication.pk}/"
    assert b"scheduled" in client.get(detail).content
    output = StringIO()
    call_command(
        "scheduled_publication",
        "reschedule",
        str(publication.pk),
        "--at",
        (at + timedelta(hours=1)).isoformat(),
        stdout=output,
    )
    assert "scheduled" in output.getvalue()
    assert b"cancelled" in client.post(detail, {"action": "cancel"}).content
    assert Publication.objects.get().status == Publication.Status.CANCELLED
    assert b"csrfmiddlewaretoken" in client.get("/scheduled/").content
    assert client.post("/scheduled/", {"text": "Bad date", "at": "bad"}).status_code == 400
    assert Publication.objects.count() == 1
    call_command("scheduled_publication", "create", "CLI edition", "--at", timezone.now().isoformat(), stdout=output)
    edition = Publication.objects.get(text="CLI edition")
    background_work.run_recovery("publish_at_deadline")
    assert_published(edition, tmp_path)
    assert b"CLI edition" in client.get(f"/scheduled/{edition.pk}/").content
    assert b"already_started" in client.post(f"/scheduled/{edition.pk}/", {"action": "cancel"}).content
