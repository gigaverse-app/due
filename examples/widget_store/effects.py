from datetime import timedelta
from typing import ClassVar

from django.db.models import Q
from django.utils import timezone

from due.django.public import ContinueAfter, DurableWorkPolicy, DurableWorkState, ExecutionEligibility, ReplaySafeEffect
from examples.widget_store import services
from examples.widget_store.models import CheckoutWork, Order


class CheckoutOrder(ReplaySafeEffect[Order, CheckoutWork]):
    deadline_wakeup_horizon: ClassVar[timedelta] = timedelta(seconds=10)

    def perform_effect(self, work: CheckoutWork) -> ContinueAfter | None:
        # Each delivery advances one exact DB stage and releases its worker slot.
        # The same obligation remains owed; losing a timer/broker message cannot
        # lose progress because the registered sweep owns the next delivery.
        order = Order.objects.get(submission_key=work.submission_key)
        stage = 11 if order.status == Order.Status.PENDING else order.progress_remaining
        delay = services.advance(work.submission_key, stage)
        order.refresh_from_db()
        if order.status in {Order.Status.CANCELLED, Order.Status.DISPATCHED}:
            return None
        assert order.next_stage_at is not None
        remaining = delay if delay is not None else (order.next_stage_at - timezone.now()).total_seconds()
        return ContinueAfter(timedelta(seconds=max(remaining, 0.001)), until=None)

    def finalize_retry(self, work: CheckoutWork, error: Exception) -> None:
        if work.state == DurableWorkState.EXHAUSTED:
            Order.objects.filter(pk=work.owner_id).update(needs_attention=True)


checkout_order = CheckoutOrder.for_owner(
    owner=Order,
    work=CheckoutWork,
    input_fields=("universe", "submission_key"),
    policy=DurableWorkPolicy(lease_duration=timedelta(seconds=20), retry_delay=timedelta(seconds=1), retention=None),
    eligibility=ExecutionEligibility(
        condition=lambda now: Q(owner__payment_status__in=("paid", "failed")) | Q(owner__expires_at__lte=now),
    ),
)
