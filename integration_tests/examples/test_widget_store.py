from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import close_old_connections
from django.test import override_settings
from django.utils import timezone

from due.django.testing import hold_nudges, make_due
from examples.widget_store.effects import checkout_order
from examples.widget_store.models import CheckoutWork, Order, Product
from examples.widget_store.services import checkout, payment, restock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def instant_stages():
    with override_settings(WIDGET_STAGE_SECONDS=0), hold_nudges():
        yield


def finish(order: Order) -> None:
    work = CheckoutWork.objects.get(owner=order)
    make_due(work)
    checkout_order.attempt_execution(work.pk)


def test_checkout_payment_dispatch_and_duplicate_requests():
    restock()
    key = uuid4()
    order = checkout(key)
    assert checkout(key).pk == order.pk
    assert Product.objects.get().inventory == 99
    finish(order)
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING
    payment(key, "paid")
    payment(key, "paid")
    finish(order)
    order.refresh_from_db()
    assert (order.status, order.progress_remaining) == (Order.Status.DISPATCHED, 0)
    assert Product.objects.get().inventory == 99
    assert CheckoutWork.objects.count() == 1
    assert checkout(key).pk == order.pk
    with pytest.raises(ValueError, match="already"):
        payment(key, "failed")


@pytest.mark.parametrize("decision", ["failed", "expired"])
def test_compensation_happens_once_even_after_replay(decision):
    restock()
    order = checkout(uuid4())
    if decision == "expired":
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    else:
        payment(order.submission_key, decision)
    work = CheckoutWork.objects.get(owner=order)
    checkout_order.perform_effect(work)
    checkout_order.perform_effect(work)
    order.refresh_from_db()
    assert (order.status, order.progress_remaining) == (Order.Status.CANCELLED, 10)
    assert Product.objects.get().inventory == 100
    with pytest.raises(ValueError, match="closed|already"):
        payment(order.submission_key, "paid")


def test_last_widget_cannot_be_reserved_twice():
    restock()
    Product.objects.update(inventory=1)

    def buy(_: int) -> str:
        close_old_connections()
        try:
            with hold_nudges():
                return checkout(uuid4()).status
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(buy, range(2)))
    assert sorted(statuses) == sorted([Order.Status.PENDING, Order.Status.CANCELLED])
    assert Product.objects.get().inventory == 0
    assert CheckoutWork.objects.count() == 1


def test_checkout_rolls_back_stock_and_order_if_admission_fails(monkeypatch):
    restock()

    def fail(*args, **kwargs):
        raise RuntimeError("admission unavailable")

    monkeypatch.setattr(CheckoutWork, "save", fail)
    with pytest.raises(RuntimeError, match="admission"):
        checkout(uuid4())
    assert Order.objects.count() == 0
    assert Product.objects.get().inventory == 100


def test_http_ui_and_validation(client):
    restock()
    assert client.get("/widgets/").status_code == 200
    key = uuid4()
    response = client.post(f"/widgets/checkout/{key}")
    assert response.status_code == 200
    assert response.json()["payment_id"] == str(key)
    assert client.post(f"/widgets/payment_webhook/{key}/invalid").status_code == 400
    assert client.post("/widgets/crash_application").status_code == 409
