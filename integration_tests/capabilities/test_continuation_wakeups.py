# ruff: noqa: F811
"""The storefront's real timed stages run without Beat; hints carry no authority."""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from due.django.conformance.background import BackgroundWork, background_work, scheduler_clock  # noqa: F401
from examples.widget_store import services
from examples.widget_store.models import CheckoutWork, Order, Product

pytestmark = pytest.mark.django_db(transaction=True)
HINT = "due.django.runtime.tasks.wake_scheduled_work_task"


def test_timed_dispatch_needs_no_beat_or_extra_rows(background_work: BackgroundWork, settings) -> None:
    settings.WIDGET_STAGE_SECONDS = 1
    services.restock()
    order = services.checkout(uuid4())
    with CaptureQueriesContext(connection) as queries:
        services.payment(order.submission_key, "paid")
        for completed in range(1, 11):
            background_work.advance(1)
            order.refresh_from_db()
            assert order.progress_remaining == 10 - completed
    order.refresh_from_db()
    work = CheckoutWork.objects.get(owner=order)
    assert order.status == Order.Status.DISPATCHED
    assert work.state == "succeeded"
    assert work.rearm_count == 0
    assert Product.objects.get(pk=order.product_id).inventory == 99
    assert CheckoutWork.objects.count() == 1
    assert not [q for q in queries if q["sql"].split()[0] == "INSERT"]
    hints = [d for d in background_work.dispatches if d.name == HINT]
    assert len(hints) == 10
    assert all(d.options["delivery_mode"] == 1 for d in hints)
