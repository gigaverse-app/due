from django.db import transaction

from examples.orders.effects import print_order
from examples.orders.models import PrintOrder, PrintWork
from examples.orders.provider import PrintReceipt


@transaction.atomic
def order_print(title: str) -> PrintWork:
    order = PrintOrder(title=title)
    order.full_clean(exclude=["provider_order_id"])
    order.save()
    with print_order.atomic(order.pk) as pending:
        return pending.request_work(PrintWork(reference=order.reference, title=title, universe="example"))


def receive_printer_receipt(receipt: PrintReceipt) -> bool:
    """
    The printer's callback: attach a receipt to the order it names, if that order is still owed.

    Correlates by the captured reference and title, then records the receipt as
    evidence for the order's sole possible submission. Registered recovery
    settles the product from it; this handler never submits or resends.
    """
    work = PrintWork.objects.filter(reference=receipt.reference, title=receipt.title).first()
    if work is None:
        return False
    return print_order.record_observed_effect_evidence(work.pk, receipt)
