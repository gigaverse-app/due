import time
from datetime import timedelta

from django.db.models import Q

from due.django.public import DurableWorkPolicy, DurableWorkState, ExecutionEligibility, ReplaySafeEffect
from examples.widget_store import services
from examples.widget_store.models import CheckoutWork, Order


class CheckoutOrder(ReplaySafeEffect[Order, CheckoutWork]):
    def perform_effect(self, work: CheckoutWork) -> None:
        # PostgreSQL owns progress and compensation. Death after any committed
        # transition is safe: repeat this command, never blindly repeat a decrement.
        for stage in range(11, 0, -1):
            while (delay := services.advance(work.submission_key, stage)) is not None:
                if delay:
                    time.sleep(delay)

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
