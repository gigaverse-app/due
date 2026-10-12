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


def test_early_duplicate_and_old_hints_cannot_advance_another_stage(background_work: BackgroundWork, settings) -> None:
    from examples.widget_store.effects import checkout_order

    settings.WIDGET_STAGE_SECONDS = 1
    order = services.checkout(uuid4())
    services.payment(order.submission_key, "paid")
    work = CheckoutWork.objects.get(owner=order)
    hint = next(d for d in background_work.dispatches if d.name == HINT)
    with CaptureQueriesContext(connection) as queries:
        checkout_order.enqueue_execution(work.pk)  # Duplicate publication, no reservation.
        background_work.deliver(hint)  # A worker clock ahead of PostgreSQL.
    assert not [q for q in queries if q["sql"].split()[0] in {"INSERT", "UPDATE", "DELETE"}]
    order.refresh_from_db()
    assert order.progress_remaining == 10
    background_work.advance(1)  # The duplicate still runs; exactly one stage is due.
    order.refresh_from_db()
    assert order.progress_remaining == 9
    with CaptureQueriesContext(connection) as queries:
        background_work.deliver(hint)  # Same revision, obsolete stage deadline.
    assert not [q for q in queries if q["sql"].split()[0] in {"INSERT", "UPDATE", "DELETE"}]
    background_work.advance(1)
    order.refresh_from_db()
    assert order.progress_remaining == 8


@pytest.mark.parametrize("fault", ["lost", "broker error"])
def test_every_hint_can_be_lost_without_losing_dispatch_or_retry_budget(
    background_work: BackgroundWork, settings, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from celery import Task

    from due.django.runtime.tasks import wake_scheduled_work_task

    settings.WIDGET_STAGE_SECONDS = 1
    send = Task.apply_async

    def fail_hint(task, *args, **kwargs):
        if task.name == HINT:
            raise ConnectionError("broker refused the optional hint")
        return send(task, *args, **kwargs)

    if fault == "broker error":
        monkeypatch.setattr(Task, "apply_async", fail_hint)
    else:
        background_work.pause(wake_scheduled_work_task)
    order = services.checkout(uuid4())
    services.payment(order.submission_key, "paid")
    for completed in range(1, 11):
        background_work.drop_held(wake_scheduled_work_task)
        background_work.advance(1)
        background_work.run_recovery("checkout_order")
        order.refresh_from_db()
        assert order.progress_remaining == 10 - completed
    work = CheckoutWork.objects.get(owner=order)
    assert (order.status, work.state, work.rearm_count) == ("dispatched", "succeeded", 0)
    assert CheckoutWork.objects.count() == 1


def test_matching_hint_still_requires_current_eligibility(background_work: BackgroundWork, settings) -> None:
    from examples.widget_store.effects import checkout_order

    settings.WIDGET_STAGE_SECONDS = 1
    order = services.checkout(uuid4())
    services.payment(order.submission_key, "paid")
    work = CheckoutWork.objects.get(owner=order)
    # Fault injection: gate facts change after publication, before atomic claim.
    Order.objects.filter(pk=order.pk).update(payment_status="")
    background_work.advance(1)
    order.refresh_from_db()
    work.refresh_from_db()
    assert order.progress_remaining == 10
    assert work.state == "ready"
    # Positive control: exactly the same due hint can enter when the gate opens.
    Order.objects.filter(pk=order.pk).update(payment_status="paid")  # Remove the injected gate fault.
    checkout_order.deliver_deadline_hint(work.pk, work.desired_revision, work.available_at)
    order.refresh_from_db()
    assert order.progress_remaining == 9


@pytest.mark.parametrize("ending", ["expired", "superseded"])
def test_ended_or_stale_continuation_cannot_send_a_hint(
    background_work: BackgroundWork, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    from datetime import timedelta

    from django.utils import timezone

    from due.django.public import ContinueAfter, supersede_work
    from examples.widget_store.effects import checkout_order

    def end(self, work):
        if ending == "superseded":
            supersede_work(CheckoutWork, work.pk)
        return ContinueAfter(timedelta(seconds=1), until=timezone.now() if ending == "expired" else None)

    monkeypatch.setattr(type(checkout_order), "transition", end)
    order = services.checkout(uuid4())
    services.payment(order.submission_key, "paid")
    work = CheckoutWork.objects.get(owner=order)
    assert work.state == ("exhausted" if ending == "expired" else "ready")
    assert work.desired_revision == (1 if ending == "expired" else 2)
    assert not [d for d in background_work.dispatches if d.name == HINT]


def test_timer_worker_advances_directly_with_registered_limits(background_work: BackgroundWork, settings) -> None:
    from due.django.runtime.names import RUN_TASK
    from examples.widget_store.registration import REGISTRATION

    settings.WIDGET_STAGE_SECONDS = 1
    order = services.checkout(uuid4())
    services.payment(order.submission_key, "paid")
    before = len([d for d in background_work.dispatches if d.name == RUN_TASK])
    hint = next(d for d in background_work.dispatches if d.name == HINT)
    background_work.advance(1)
    order.refresh_from_db()
    assert order.progress_remaining == 9
    assert len([d for d in background_work.dispatches if d.name == RUN_TASK]) == before
    assert hint.options["time_limit"] == REGISTRATION.execution_time_limit
    assert hint.options["soft_time_limit"] == REGISTRATION.execution_soft_time_limit
