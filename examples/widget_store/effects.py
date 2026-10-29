from datetime import timedelta
from typing import ClassVar

from django.conf import settings
from django.db.models import Q

from due.django.public import (
    ContinueAfter,
    DatabaseEffect,
    DatabaseTime,
    DurableWorkPolicy,
    DurableWorkState,
    ExecutionEligibility,
)
from examples.widget_store import services
from examples.widget_store.models import CheckoutWork, Order


class CheckoutOrder(DatabaseEffect[Order, CheckoutWork]):
    deadline_wakeup_horizon: ClassVar[timedelta] = timedelta(seconds=10)

    def transition(self, work: CheckoutWork) -> ContinueAfter | None:
        services.advance(work)
        assert work.owner is not None
        if work.owner.status in {Order.Status.CANCELLED, Order.Status.DISPATCHED}:
            return None
        return ContinueAfter(timedelta(seconds=max(settings.WIDGET_STAGE_SECONDS, 0.001)), until=None)

    def finalize_retry(self, work: CheckoutWork, error: Exception) -> None:
        if work.state == DurableWorkState.EXHAUSTED:
            Order.objects.filter(pk=work.owner_id).update(needs_attention=True)


checkout_order = CheckoutOrder.for_owner(
    owner=Order,
    work=CheckoutWork,
    input_fields=("universe", "submission_key"),
    policy=DurableWorkPolicy(lease_duration=timedelta(seconds=20), retry_delay=timedelta(seconds=1), retention=None),
    # Static, so each claim judges expiry at the time it selects the row, without a clock read of its own.
    eligibility=ExecutionEligibility(
        condition=Q(owner__payment_status__in=("paid", "failed")) | Q(owner__expires_at__lte=DatabaseTime()),
    ),
)
