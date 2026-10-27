"""The browser, CLI and conformance bindings share these application commands."""

from datetime import datetime
from uuid import UUID, uuid7

from django.db import transaction
from django.utils import timezone

from due.django.capabilities.scheduled import CancellationResult, ScheduleResult
from examples.scheduled.effects import publish_at_deadline
from examples.scheduled.models import Publication, PublicationDeadline


def _check_deadline(at: datetime) -> None:
    if timezone.is_naive(at):
        raise ValueError("Publication time must include a timezone")


class SubmissionConflict(ValueError):
    """A submission identity was already used for a different initial request."""


@transaction.atomic
def create_publication(text: str, at: datetime, *, submission_key: UUID | None = None) -> Publication:
    _check_deadline(at)
    candidate = Publication(
        text=text,
        publish_at=at,
        initial_publish_at=at,
        submission_key=submission_key if submission_key is not None else uuid7(),
    )
    candidate.full_clean(validate_unique=False)
    # The row's unique key arbitrates concurrent callers; get_or_create retries
    # the lookup after a competing insert commits. Intent joins the same commit.
    publication, created = Publication.objects.get_or_create(
        submission_key=candidate.submission_key,
        defaults={"text": candidate.text, "publish_at": at, "initial_publish_at": at},
    )
    if created:
        reschedule_publication(publication.pk, at)
    elif publication.text != text or publication.initial_publish_at != at:
        raise SubmissionConflict("Submission key was already used for a different request")
    # Compare original input, not the mutable deadline/status: a lost response
    # retried after cancellation or rescheduling must never undo that change.
    return publication


def reschedule_publication(publication_id: int, at: datetime) -> ScheduleResult:
    _check_deadline(at)
    with publish_at_deadline.atomic(publication_id) as pending:
        publication = pending.locked_owner
        result = publish_at_deadline.schedule_at(
            publication_id, PublicationDeadline(text=publication.text, universe="example"), at=at
        )
        # Refused changes must not make the product advertise a new deadline.
        # The owner lock makes this product mirror and admission one transaction.
        if result == ScheduleResult.SCHEDULED:
            publication.publish_at = at
            publication.status = Publication.Status.SCHEDULED
            publication.save(update_fields=["publish_at", "status"])
        return result


def cancel_publication(publication_id: int) -> CancellationResult:
    with publish_at_deadline.atomic(publication_id, notify_worker=False):
        result = publish_at_deadline.cancel_pending(publication_id)
        if result == CancellationResult.CANCELLED:
            Publication.objects.filter(pk=publication_id).update(status=Publication.Status.CANCELLED)
        return result


def retry_publication(publication_id: int) -> bool:
    with publish_at_deadline.atomic(publication_id) as pending:
        retried = pending.retry_failed()
        if retried:
            Publication.objects.filter(pk=publication_id).update(status=Publication.Status.SCHEDULED)
        return retried
