"""The early proof implementation, before portal handoff and temporary spooling.

The final application registers effects.publish_proof. This intermediate version
uses the same work schema and is exercised directly by test_stage2.py.
"""

from due.django.public import ReplaySafeEffect

from . import providers
from .models import Job, ProofWork
from .rendering import RENDER_POLICY


class PublishProof(ReplaySafeEffect[Job, ProofWork, str]):
    def perform_effect(self, work):
        return providers.write_proof(work.text, work.provider_idempotency_key())

    def finalize_success(self, work, proof_key):
        Job.objects.filter(pk=work.owner_id).update(published_key=proof_key)


publish_proof = PublishProof.for_owner(
    owner=Job,
    work=ProofWork,
    input_fields=("universe", "revision_id", "text"),
    policy=RENDER_POLICY,
)
