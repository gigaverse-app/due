"""A replay-safe file publication with fenced database settlement."""

from due.django.public import DurableWorkState, ReplaySafeEffect
from examples.documents import storage
from examples.documents.models import Document, PublicationWork


class PublishDocument(ReplaySafeEffect[Document, PublicationWork, str]):
    def perform_effect(self, work: PublicationWork) -> str:
        # Each revision writes its own immutable key. A late old worker cannot
        # overwrite the current revision's bytes. Retrying this key is safe.
        return storage.write_revision(work.text, work.provider_idempotency_key())

    def finalize_success(self, work: PublicationWork, result: str) -> None:
        # Due holds the owner/work locks and validates the captured revision
        # and fencing token before publishing this pointer.
        Document.objects.filter(pk=work.owner_id).update(published_key=result, publication_failed=False)

    def finalize_retry(self, work: PublicationWork, error: Exception) -> None:
        if work.state == DurableWorkState.EXHAUSTED:
            Document.objects.filter(pk=work.owner_id).update(publication_failed=True)


publish_document = PublishDocument.for_owner(work=PublicationWork)


def request_publication(document_id: int, text: str) -> PublicationWork:
    """Commit product intent and its obligation together; wake only after commit."""
    with publish_document.atomic(document_id) as pending:
        document = pending.locked_owner
        document.text = text
        document.publication_failed = False
        document.save(update_fields=["text", "publication_failed"])
        return pending.request_work(PublicationWork(text=text, universe="example"))


def retry_publication(document_id: int) -> bool:
    """Explicitly grant an exhausted publication a fresh retry budget."""
    with publish_document.atomic(document_id) as pending:
        retried = pending.retry_failed()
        if retried:
            Document.objects.filter(pk=document_id).update(publication_failed=False)
        return retried
