"""Transactional business commands shared by HTTP, worker and conformance proofs."""

from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from examples.widget_store.models import CheckoutWork, Order, Product


@transaction.atomic
def restock() -> Product:
    product, _ = Product.objects.get_or_create(pk=1)
    Product.objects.filter(pk=product.pk).update(inventory=100)
    product.refresh_from_db()
    return product


@transaction.atomic
def checkout(key: UUID) -> Order:
    from examples.widget_store.effects import checkout_order

    product, _ = Product.objects.get_or_create(pk=1)
    order, created = Order.objects.get_or_create(
        submission_key=key,
        defaults={
            "product": product,
            "expires_at": timezone.now() + timedelta(seconds=settings.WIDGET_PAYMENT_TIMEOUT_SECONDS),
        },
    )
    if not created:
        return order
    reserved = Product.objects.filter(pk=product.pk, inventory__gt=0).update(inventory=F("inventory") - 1)
    order.reserved = bool(reserved)
    if not reserved:
        order.status = Order.Status.CANCELLED
    order.save(update_fields=["reserved", "status"])
    if reserved:
        with checkout_order.atomic(order.pk) as pending:
            pending.request_work(CheckoutWork(submission_key=key, universe="example"))
    return order


def payment(key: UUID, decision: str) -> Order:
    from examples.widget_store.effects import checkout_order

    if decision not in {"paid", "failed"}:
        raise ValueError("Payment must be paid or failed")
    identity = Order.objects.get(submission_key=key).pk
    with checkout_order.atomic(identity) as pending:
        order = pending.locked_owner
        if order.payment_status:
            if order.payment_status != decision:
                raise ValueError("Payment was already decided")
            return order
        if order.status != Order.Status.PENDING or timezone.now() >= order.expires_at:
            raise ValueError("Checkout is closed")
        order.payment_status = decision
        order.save(update_fields=["payment_status"])
        pending.request_progress()
        return order


def advance(work: CheckoutWork) -> None:
    """One database transition; Due commits it with the next deadline.

    The engine already holds this order's lock and this work's current fence.
    Provider I/O does not belong in this callback.
    """
    from due.django.public import require_fenced_settlement

    require_fenced_settlement(work)
    order = work.owner
    assert order is not None
    if order.status == Order.Status.PENDING:
        if order.payment_status == "paid":
            order.status = Order.Status.PAID
        elif order.payment_status == "failed" or timezone.now() >= order.expires_at:
            Product.objects.filter(pk=order.product_id).update(inventory=F("inventory") + 1)
            order.reserved = False
            order.status = Order.Status.CANCELLED
        else:
            raise ValueError("Payment is still pending")
    elif order.status == Order.Status.PAID:
        order.progress_remaining -= 1
        if order.progress_remaining == 0:
            order.status = Order.Status.DISPATCHED
    order.save(update_fields=["status", "reserved", "progress_remaining"])


def retry_order(identity: int) -> bool:
    from examples.widget_store.effects import checkout_order

    with checkout_order.atomic(identity) as pending:
        retried = pending.retry_failed()
        if retried:
            Order.objects.filter(pk=identity).update(needs_attention=False)
        return retried
