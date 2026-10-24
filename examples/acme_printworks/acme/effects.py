from datetime import timedelta

from due.django.capabilities.authoring import registered_nudge
from due.django.capabilities.compensation import IndependentCompensation
from due.django.capabilities.observed_operations import ObservedExternalOperation
from due.django.capabilities.projection import Projection
from due.django.evidence.effect_evidence import JsonAppliedEffectEvidenceStore
from due.django.public import (
    DurableWorkPolicy,
    DurableWorkState,
    OwnedDurableWorkCapability,
    StatusMirror,
)
from due.errors import EffectNotAppliedError

from . import providers
from .models import (
    CleanupWork,
    Job,
    PortalWork,
    PrintEvidence,
    PrintOrder,
    PrintWork,
    ProofWork,
)

POLICY = DurableWorkPolicy(retention=None)


class PortalProjection(Projection[Job, PortalWork]):
    def write_projection(self, work):
        providers.put_portal(work.owner_id, work.proof_key)

    def on_projected(self, work):
        Job.objects.filter(pk=work.owner_id).update(portal_failed=False)

    def finalize_retry(self, work, error):
        if work.state == DurableWorkState.EXHAUSTED:
            Job.objects.filter(pk=work.owner_id).update(portal_failed=True)


project_portal = PortalProjection.for_owner(
    owner=Job,
    work=PortalWork,
    input_fields=("universe", "proof_key"),
    policy=DurableWorkPolicy(retention=None, repair_interval=timedelta(seconds=5)),
)


class SpoolCleanup(IndependentCompensation[ProofWork, CleanupWork]):
    def capture(self, producer):
        return CleanupWork(spool_key=str(producer.token), universe="acme")

    def compensate(self, work):
        return providers.cleanup(work.spool_key)


cleanup_spool = SpoolCleanup(
    producer_model=ProofWork,
    model=CleanupWork,
    nudge=registered_nudge(CleanupWork),
    policy=DurableWorkPolicy(retention=None, max_rearms=None),
    inspection_interval=timedelta(seconds=5),
)


def render_proof(work):
    return providers.render(work.text, work.provider_idempotency_key(), str(work.fencing_token))


class PublishProof(OwnedDurableWorkCapability[Job, ProofWork, str]):
    def apply(self, work, context):
        with cleanup_spool.record_cleanup_before_call(context) as output:
            key = render_proof(work)
            if providers.cleanup(str(work.fencing_token)):
                output.confirm_compensated()
            return key

    def finalize_success(self, work, proof_key):
        Job.objects.filter(pk=work.owner_id).update(published_key=proof_key, proof_failed=False)
        # Both the pointer and the portal obligation commit in this settlement.
        with project_portal.atomic(work.owner_id) as pending:
            pending.request_work(PortalWork(proof_key=proof_key, universe="acme"))

    def finalize_retry(self, work, error):
        if work.state == DurableWorkState.EXHAUSTED:
            Job.objects.filter(pk=work.owner_id).update(proof_failed=True)


publish_proof = PublishProof.for_owner(
    owner=Job,
    work=ProofWork,
    input_fields=("universe", "revision_id", "text"),
    policy=POLICY,
)

mirror = StatusMirror(lambda work: PrintOrder.objects.filter(pk=work.owner_id))


class OrderPrinting(ObservedExternalOperation[PrintOrder, PrintWork, providers.PrintReceipt]):
    observation_interval = timedelta(seconds=5)
    evidence_store = JsonAppliedEffectEvidenceStore(PrintEvidence, providers.PrintReceipt)
    on_applied = mirror.outcome(
        lambda work, receipt: {
            "status": "accepted",
            "provider_order_id": receipt.order_id,
        }
    )
    on_unknown = mirror.outcome({"status": "unknown"})
    on_retry = mirror.outcome({"status": "retrying"})
    on_exhausted = mirror.outcome({"status": "stopped"})
    on_terminal = mirror.outcome({"status": "stopped"})

    def perform_effect(self, work):
        try:
            return providers.submit_print(str(work.reference), work.proof_key, work.quantity)
        except providers.PrinterRejected as error:
            # Only a definite refusal before acceptance permits retrying a purchase.
            raise EffectNotAppliedError(str(error)) from error

    def observe_effect(self, work):
        receipt = providers.observe_print(str(work.reference))
        if receipt is not None and (
            receipt.reference != str(work.reference)
            or receipt.proof_key != work.proof_key
            or receipt.quantity != work.quantity
        ):
            raise ValueError("Receipt does not match the captured print request")
        return receipt


order_printing = OrderPrinting.for_owner(
    owner=PrintOrder,
    work=PrintWork,
    input_fields=("universe", "reference", "proof_key", "quantity"),
    policy=POLICY,
)
