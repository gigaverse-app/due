"""The browser, CLI and conformance bindings share these application commands."""

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from due.django.capabilities.scheduled import CancellationResult, ScheduleResult
from examples.scheduled.effects import publish_at_deadline
from examples.scheduled.models import Publication, PublicationDeadline


def _check_deadline(at: datetime) -> None:
    if timezone.is_naive(at):
        raise ValueError("Publication time must include a timezone")


@transaction.atomic
def create_publication(text: str, at: datetime) -> Publication:
    _check_deadline(at)
    publication = Publication(text=text, publish_at=at)
    publication.full_clean()
    publication.save()
    reschedule_publication(publication.pk, at)
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
