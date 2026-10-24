import pytest
from due.django.testing import hold_nudges

from acme import providers
from acme.models import Job
from acme.services import add_revision, request_proof
from acme.stage2 import publish_proof


def test_worker_budget_allows_the_ten_minute_story():
    from datetime import timedelta

    from due.django.runtime.registry import registration_for

    from acme.models import ProofWork

    registration = registration_for(ProofWork)
    assert 10 * 60 < registration.execution_soft_time_limit < registration.execution_time_limit
    assert timedelta(seconds=registration.execution_time_limit) < registration.capability.policy.lease_duration
    assert publish_proof.policy.lease_duration == registration.capability.policy.lease_duration


@pytest.mark.django_db(transaction=True)
def test_intermediate_replay_safe_proof():
    with hold_nudges():
        job = Job.objects.create()
        revision = add_revision(job.pk, 17, "Opening day: June 12")
        work = request_proof(revision.pk)
        publish_proof.attempt_execution(work.pk)
    job.refresh_from_db()
    assert providers.proof_text(job.published_key) == revision.text
