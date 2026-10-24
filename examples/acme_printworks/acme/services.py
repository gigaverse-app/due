from django.db import transaction

from .effects import order_printing, project_portal, publish_proof
from .models import Job, PrintOrder, PrintWork, ProofWork, Revision
from .tasks import count_words


@transaction.atomic
def add_revision(job_id, number, text):
    revision = Revision.objects.create(job_id=job_id, number=number, text=text)
    count_words.enqueue(revision.pk)
    return revision


def request_proof(revision_id):
    revision = Revision.objects.get(pk=revision_id)
    with publish_proof.atomic(revision.job_id) as pending:
        job = pending.locked_owner
        job.current_revision = revision
        job.published_key = ""  # Approval waits for this revision's proof.
        job.save(update_fields=["current_revision", "published_key"])
        return pending.request_work(
            ProofWork(
                revision_id=revision.pk,
                text=revision.text,
                universe="acme",
            )
        )


@transaction.atomic
def approve_print(job_id, expected_proof_key, quantity=500):
    job = Job.objects.select_for_update().get(pk=job_id)
    if not job.published_key or job.published_key != expected_proof_key:
        raise ValueError("Proof changed; review the current proof before approving")
    order = PrintOrder.objects.create(
        revision=job.current_revision,
        proof_key=job.published_key,
        quantity=quantity,
    )
    with order_printing.atomic(order.pk) as pending:
        pending.request_work(
            PrintWork(
                reference=order.reference,
                proof_key=order.proof_key,
                quantity=order.quantity,
                universe="acme",
            )
        )
    return order


def retry_proof(job_id):
    with publish_proof.atomic(job_id) as pending:
        if pending.retry_failed():
            Job.objects.filter(pk=job_id).update(proof_failed=False)
            return True
    return False


def retry_portal(job_id):
    with project_portal.atomic(job_id) as pending:
        if pending.retry_failed():
            Job.objects.filter(pk=job_id).update(portal_failed=False)
            return True
    return False
