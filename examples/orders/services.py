from django.db import transaction

from examples.orders.effects import print_order
from examples.orders.models import PrintOrder, PrintWork


@transaction.atomic
def order_print(title: str) -> PrintWork:
    order = PrintOrder(title=title)
    order.full_clean(exclude=["provider_order_id"])
    order.save()
    with print_order.atomic(order.pk) as pending:
        return pending.request_work(PrintWork(reference=order.reference, title=title, universe="example"))
