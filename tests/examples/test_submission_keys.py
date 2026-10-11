# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import connection, connections, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from due.django.testing import hold_nudges
from examples.scheduled.models import Publication, PublicationDeadline
from examples.scheduled.services import cancel_publication, create_publication, reschedule_publication

pytestmark = pytest.mark.django_db(transaction=True)


def test_replay_has_no_writes_or_additional_rows() -> None:
    key, at = uuid4(), timezone.now() + timedelta(days=1)
    with hold_nudges():
        original = create_publication("One edition", at, submission_key=key)
    with CaptureQueriesContext(connection) as queries:
        replay = create_publication("One edition", at, submission_key=key)
    assert replay.pk == original.pk
    assert Publication.objects.count() == PublicationDeadline.objects.count() == 1
    assert not [q for q in queries if q["sql"].split()[0] in {"INSERT", "UPDATE", "DELETE"}]


@pytest.mark.parametrize("change", ["cancel", "reschedule"])
def test_retry_does_not_reset_current_publication(change: str) -> None:
    key, at = uuid4(), timezone.now() + timedelta(days=1)
    with hold_nudges():
        publication = create_publication("Original", at, submission_key=key)
        if change == "cancel":
            cancel_publication(publication.pk)
        else:
            reschedule_publication(publication.pk, at + timedelta(days=1))
    publication.refresh_from_db()
    replay = create_publication("Original", at, submission_key=key)
    assert (replay.pk, replay.status, replay.publish_at) == (publication.pk, publication.status, publication.publish_at)
    with pytest.raises(ValueError, match="different"):
        create_publication("Changed", at, submission_key=key)
    with pytest.raises(ValueError, match="different"):
        create_publication("Original", at + timedelta(seconds=1), submission_key=key)


def test_concurrent_submissions_share_one_committed_identity() -> None:
    key, at, barrier = uuid4(), timezone.now() + timedelta(days=1), Barrier(4)

    def submit(_: int) -> int:
        try:
            barrier.wait(timeout=10)
            return create_publication("Concurrent", at, submission_key=key).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(submit, range(4)))
    assert len(set(ids)) == 1
    assert Publication.objects.count() == PublicationDeadline.objects.count() == 1


def test_rollback_does_not_consume_submission_key() -> None:
    key, at = uuid4(), timezone.now() + timedelta(days=1)
    with pytest.raises(RuntimeError), transaction.atomic():
        create_publication("Rollback", at, submission_key=key)
        raise RuntimeError("rollback")
    assert not Publication.objects.exists()
    assert not PublicationDeadline.objects.exists()
    with hold_nudges():
        create_publication("Retry", at, submission_key=key)


def test_browser_repost_and_conflict(client: Client) -> None:
    key = client.get("/scheduled/").context["form"]["submission_key"].value()
    data = {"submission_key": str(key), "text": "Browser", "at": (timezone.now() + timedelta(days=1)).isoformat()}
    with hold_nudges():
        first = client.post("/scheduled/", data)
        second = client.post("/scheduled/", data)
    assert first.status_code == second.status_code == 302
    assert first.headers["Location"] == second.headers["Location"]
    data["text"] = "Different"
    assert client.post("/scheduled/", data).status_code == 409
    assert Publication.objects.count() == 1


def test_cli_reuses_explicit_key() -> None:
    from io import StringIO

    from django.core.management import call_command

    key, at, output = uuid4(), timezone.now() + timedelta(days=1), StringIO()
    with hold_nudges():
        for _ in range(2):
            call_command(
                "scheduled_publication",
                "create",
                "CLI retry",
                "--at",
                at.isoformat(),
                "--submission-key",
                str(key),
                stdout=output,
            )
    assert Publication.objects.count() == PublicationDeadline.objects.count() == 1
    assert str(key) in output.getvalue()
