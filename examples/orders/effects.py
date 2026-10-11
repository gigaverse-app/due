from datetime import timedelta

from due.django.capabilities.observed_operations import ObservedExternalOperation
from due.django.evidence.effect_evidence import JsonAppliedEffectEvidenceStore
from due.django.public import DurableWorkPolicy, StatusMirror
from due.errors import EffectNotAppliedError
from examples.orders import provider
from examples.orders.models import PrintEvidence, PrintOrder, PrintWork
from examples.orders.provider import PrintReceipt

mirror = StatusMirror(lambda work: PrintOrder.objects.filter(pk=work.owner_id))


class OrderPrinting(ObservedExternalOperation[PrintOrder, PrintWork, PrintReceipt]):
    observation_interval = timedelta(seconds=5)
    evidence_store = JsonAppliedEffectEvidenceStore(PrintEvidence, PrintReceipt)
    on_applied = mirror.outcome(lambda work, result: {"provider_order_id": result.order_id, "status": "printed"})
    on_unknown = mirror.outcome({"status": "unknown"})
    on_retry = mirror.outcome({"status": "retrying"})
    on_exhausted = mirror.outcome({"status": "stopped"})
    on_terminal = mirror.outcome({"status": "stopped"})

    def perform_effect(self, work: PrintWork) -> PrintReceipt:
        try:
            return provider.submit(str(work.reference), work.title)
        except provider.PrinterRejected as error:
            raise EffectNotAppliedError(str(error)) from error

    def observe_effect(self, work: PrintWork) -> PrintReceipt | None:
        receipt = provider.observe(str(work.reference))
        if receipt is not None and (receipt.reference != str(work.reference) or receipt.title != work.title):
            raise ValueError("Printer receipt does not match the captured request")
        return receipt


print_order = OrderPrinting.for_owner(
    owner=PrintOrder,
    work=PrintWork,
    input_fields=("universe", "reference", "title"),
    policy=DurableWorkPolicy(retention=None),
)
