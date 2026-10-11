from due.django.capabilities.scheduled import ActionOutcome, ScheduledAction
from due.django.public import DurableWorkPolicy, DurableWorkState
from examples.documents import storage
from examples.scheduled.models import Publication, PublicationDeadline


class PublishAtDeadline(ScheduledAction[Publication, PublicationDeadline]):
    def is_still_needed(self, work: PublicationDeadline) -> bool:
        return Publication.objects.filter(pk=work.owner_id, status=Publication.Status.SCHEDULED).exists()

    def perform_effect(self, work: PublicationDeadline) -> None:
        # A revision owns one immutable provider key: recovery may repeat this
        # write after process death, without creating a second publication.
        storage.write_revision(work.text, work.provider_idempotency_key())

    def on_completed(self, work: PublicationDeadline, outcome: ActionOutcome) -> None:
        if outcome == ActionOutcome.PERFORMED:
            # Due fences this callback against the claimed revision/token. A
            # stale message cannot publish the pointer of a replacement deadline.
            Publication.objects.filter(pk=work.owner_id).update(
                status=Publication.Status.PUBLISHED,
                published_key=storage.revision_key(work.provider_idempotency_key()),
            )

    def finalize_retry(self, work: PublicationDeadline, error: Exception) -> None:
        if work.state == DurableWorkState.EXHAUSTED:
            Publication.objects.filter(pk=work.owner_id).update(status=Publication.Status.FAILED)


publish_at_deadline = PublishAtDeadline.for_owner(
    owner=Publication,
    work=PublicationDeadline,
    input_fields=("universe", "text", "run_at", "cancelled"),
    policy=DurableWorkPolicy(retention=None),
)
