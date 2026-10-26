"""Plain Celery baseline: useful delivery options, without a custom outbox/reaper."""

from functools import partial
from uuid import uuid7

from celery import shared_task
from django.db import transaction

from examples.documents import storage
from examples.documents.models import Document


@shared_task(
    autoretry_for=(OSError,),
    max_retries=3,
    default_retry_delay=1,
    acks_late=True,
    reject_on_worker_lost=True,
)
def publish_document_task(document_id: int, text: str, operation_key: str) -> str:
    key = storage.write_revision(text, operation_key)
    # This ordinary task has no revision/token check before updating the pointer.
    Document.objects.filter(pk=document_id).update(published_key=key, publication_failed=False)
    return key


@transaction.atomic
def request_publication(document_id: int, text: str) -> str:
    document = Document.objects.select_for_update().get(pk=document_id)
    document.text = text
    document.publication_failed = False
    document.save(update_fields=["text", "publication_failed"])
    operation_key = uuid7().hex
    # on_commit avoids executing against uncommitted data. It does not persist
    # an executable obligation; the only delivery record will be in the broker.
    transaction.on_commit(partial(publish_document_task.delay, document.pk, text, operation_key))
    return operation_key
