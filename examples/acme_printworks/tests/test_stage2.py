import pytest
from due.django.testing import hold_nudges

from acme import providers
from acme.models import Job
from acme.services import add_revision, request_proof
from acme.stage2 import publish_proof


@pytest.mark.django_db(transaction=True)
def test_intermediate_replay_safe_proof():
    with hold_nudges():
        job = Job.objects.create()
        revision = add_revision(job.pk, 17, "Opening day: June 12")
        work = request_proof(revision.pk)
        publish_proof.attempt_execution(work.pk)
    job.refresh_from_db()
    assert providers.proof_text(job.published_key) == revision.text
