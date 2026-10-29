"""
Database round trips of one widget dispatch stage, delivered by its deadline hint.

Each paid order runs ten such stages, so a statement here is paid ten times per
order. The budget is exact: a separate clock read or a savepoint around the
single release UPDATE fails here rather than showing up as database load.
"""

from uuid import uuid4

import pytest
from django.test import override_settings

from due.django.runtime.tasks import wake_scheduled_work_task
from due.django.testing import hold_nudges, make_due
from examples.widget_store.effects import checkout_order
from examples.widget_store.models import CheckoutWork, Order
from examples.widget_store.services import checkout, payment, restock
from tests.harness.round_trips import (
    CLOCK,
    LOCK_OWNER,
    LOCK_WORK,
    READ_WORK,
    UPDATE_OWNER,
    UPDATE_WORK,
    RoundTrips,
)

pytestmark = pytest.mark.django_db(transaction=True)

_round_trips = RoundTrips(work=CheckoutWork, owner=Order).of


@pytest.fixture
def paid_work() -> CheckoutWork:
    with override_settings(WIDGET_STAGE_SECONDS=1), hold_nudges():
        restock()
        key = uuid4()
        order = checkout(key)
        payment(key, "paid")
        yield CheckoutWork.objects.get(owner=order)


def test_a_hinted_stage_claims_transitions_and_releases_with_one_clock_read(paid_work: CheckoutWork):
    make_due(paid_work)
    paid_work.refresh_from_db()
    with hold_nudges() as held:
        trips = _round_trips(
            lambda: checkout_order.deliver_deadline_hint(
                paid_work.pk, paid_work.desired_revision, paid_work.available_at
            )
        )
    assert trips == [
        # The hint grants nothing: due now at this exact revision and deadline, judged by the statement.
        READ_WORK,
        # The callable eligibility (payment decided, or reservation expired) needs a concrete time.
        CLOCK,
        LOCK_WORK,
        UPDATE_WORK,
        # The database transition: owner before work, the product write, then the release.
        LOCK_OWNER,
        LOCK_WORK,
        UPDATE_OWNER,
        UPDATE_WORK,
    ]
    assert [nudge.work_id for nudge in held] == [paid_work.pk]
    paid_work.refresh_from_db()
    assert paid_work.state == "ready"


def test_rearming_the_next_stage_hint_is_one_read(paid_work: CheckoutWork, monkeypatch: pytest.MonkeyPatch):
    make_due(paid_work)
    paid_work.refresh_from_db()
    with override_settings(WIDGET_STAGE_SECONDS=1), hold_nudges():
        checkout_order.deliver_deadline_hint(paid_work.pk, paid_work.desired_revision, paid_work.available_at)
    published: list[tuple] = []
    monkeypatch.setattr(wake_scheduled_work_task, "apply_async", lambda args, **options: published.append(args))

    # The released stage is due within the hint horizon: one read finds it, and no claim is taken.
    assert _round_trips(lambda: checkout_order.enqueue_execution(paid_work.pk)) == [READ_WORK]
    paid_work.refresh_from_db()
    assert [args[2:] for args in published] == [(paid_work.desired_revision, paid_work.available_at.isoformat())]
