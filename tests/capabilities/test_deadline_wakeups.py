# ruff: noqa: F811
"""Shared scheduled transport invariants, exercised through a registered adopter."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from due.django.conformance.background import BackgroundWork, background_work, scheduler_clock  # noqa: F401
from due.django.testing import hold_nudges
from examples.scheduled.effects import publish_at_deadline
from examples.scheduled.models import Publication
from examples.scheduled.services import cancel_publication, create_publication, reschedule_publication

pytestmark = pytest.mark.django_db(transaction=True)
HINT = "due.django.runtime.tasks.wake_scheduled_work_task"


@pytest.fixture(autouse=True)
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUE_OUTPUT_DIR", str(tmp_path))


def test_deadline_hint_needs_no_beat_and_no_early_reservation(background_work: BackgroundWork, tmp_path: Path) -> None:
    publication = create_publication("Near deadline", timezone.now() + timedelta(seconds=3))
    work = publication.publicationdeadline
    assert work.state == "ready"
    assert len([d for d in background_work.dispatches if d.name == HINT]) == 1
    background_work.advance(2)
    assert list(tmp_path.iterdir()) == []
    background_work.advance(1)
    publication.refresh_from_db()
    assert publication.status == Publication.Status.PUBLISHED
    assert (tmp_path / publication.published_key).read_text() == publication.text


def test_hint_publication_and_early_delivery_write_nothing(background_work: BackgroundWork) -> None:
    with hold_nudges():
        publication = create_publication("No hint ledger", timezone.now() + timedelta(seconds=3))
    work = publication.publicationdeadline
    with CaptureQueriesContext(connection) as queries:
        publish_at_deadline.enqueue_execution(work.pk)
        hint = next(d for d in background_work.dispatches if d.name == HINT)
        background_work.deliver(hint)  # Simulate a worker clock ahead of PostgreSQL.
    writes = [q["sql"] for q in queries if q["sql"].split()[0] in {"INSERT", "UPDATE", "DELETE"}]
    assert writes == []
    work.refresh_from_db()
    assert work.state == "ready"


def test_far_deadline_enters_lookahead_and_survives_duplicate_hints(
    background_work: BackgroundWork, tmp_path: Path
) -> None:
    publication = create_publication("Far deadline", timezone.now() + timedelta(seconds=60))
    assert not [d for d in background_work.dispatches if d.name == HINT]
    background_work.advance(51)
    background_work.run_recovery("publish_at_deadline")
    background_work.run_recovery("publish_at_deadline")
    assert len([d for d in background_work.dispatches if d.name == HINT]) == 2
    background_work.advance(9)
    publication.refresh_from_db()
    assert publication.status == Publication.Status.PUBLISHED
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("change", ["cancel", "later", "backoff"])
def test_obsolete_hint_cannot_publish(background_work: BackgroundWork, tmp_path: Path, change: str) -> None:
    at = timezone.now() + timedelta(seconds=3)
    publication = create_publication("Changed", at)
    if change == "cancel":
        cancel_publication(publication.pk)
    elif change == "later":
        reschedule_publication(publication.pk, at + timedelta(seconds=60))
    else:
        work = publication.publicationdeadline
        work.available_at = at + timedelta(seconds=60)
        work.save(update_fields=["available_at"])
    background_work.advance(3)
    assert list(tmp_path.iterdir()) == []


def test_lost_timer_message_recovers_at_deadline(background_work: BackgroundWork, tmp_path: Path) -> None:
    publication = create_publication("Lost timer", timezone.now() + timedelta(seconds=3))
    from due.django.runtime.tasks import wake_scheduled_work_task

    background_work.drop_held(wake_scheduled_work_task)
    background_work.advance(3)
    assert list(tmp_path.iterdir()) == []
    background_work.run_recovery("publish_at_deadline")
    publication.refresh_from_db()
    assert (tmp_path / publication.published_key).read_text() == publication.text


def test_lookahead_is_bounded_read_only_and_routes_to_registered_queue(background_work: BackgroundWork) -> None:
    from dataclasses import replace

    from due.django.runtime.registry import registration_for
    from examples.scheduled.models import PublicationDeadline

    with hold_nudges():
        for i in range(4):
            create_publication(str(i), timezone.now() + timedelta(seconds=2 + i))
    bounded = replace(publish_at_deadline, policy=replace(publish_at_deadline.policy, batch_size=2))
    with CaptureQueriesContext(connection) as queries:
        bounded.recover()
    hints = [d for d in background_work.dispatches if d.name == HINT]
    assert len(hints) == 2
    assert all(d.options["delivery_mode"] == 1 for d in hints)
    assert [d.eta for d in hints] == [timezone.now() + timedelta(seconds=i) for i in (2, 3)]
    assert all(d.options["queue"] == registration_for(PublicationDeadline).queue for d in hints)
    assert not [q for q in queries if q["sql"].split()[0] in {"INSERT", "UPDATE", "DELETE"}]


def test_failed_hint_does_not_block_due_work_or_other_hints(
    background_work: BackgroundWork, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from due.django.models import RecoveryDeferral
    from due.django.runtime.recovery_pass import RecoveryIncomplete
    from due.django.runtime.tasks import wake_scheduled_work_task

    with hold_nudges():
        due = create_publication("Due now", timezone.now())
        bad = create_publication("Bad hint", timezone.now() + timedelta(seconds=2))
        good = create_publication("Good hint", timezone.now() + timedelta(seconds=3))
    bad_id = bad.publicationdeadline.pk
    send = wake_scheduled_work_task.apply_async

    def fail_one(*args: Any, **kwargs: Any) -> object:
        if kwargs["args"][1] == bad_id:
            raise ConnectionError("broker unavailable for one hint")
        return send(*args, **kwargs)

    monkeypatch.setattr(wake_scheduled_work_task, "apply_async", fail_one)
    with pytest.raises(RecoveryIncomplete):
        publish_at_deadline.recover()
    due.refresh_from_db()
    assert (tmp_path / due.published_key).read_text() == due.text
    assert not RecoveryDeferral.objects.exists()
    background_work.advance(3)
    good.refresh_from_db()
    assert good.status == Publication.Status.PUBLISHED
