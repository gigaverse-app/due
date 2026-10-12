"""Mutations run the same generated contracts that CI requires for the application."""

import pytest
from pytest_obligation import contract_cases
from pytest_obligation.crash_histories import HistoriesDiverged
from pytest_obligation.process_histories import assert_process_deaths_converge

from due.django.conformance.suite import effect_contract
from examples.widget_store import services
from examples.widget_store.effects import checkout_order
from examples.widget_store.models import Order, Product
from integration_tests.conformance.widget_examples import EXAMPLES

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("mutation", ["wrong dispatch", "double dispatch"])
def test_generated_replay_rejects_corrupt_progress(monkeypatch, mutation):
    original = services.advance

    def corrupt(key, stage):
        already_done = Order.objects.get(submission_key=key).status == Order.Status.DISPATCHED
        result = original(key, stage)
        if stage == 1 and (mutation == "wrong dispatch" or already_done):
            order = Order.objects.get(submission_key=key)
            order.progress_remaining = 1 if mutation == "wrong dispatch" else order.progress_remaining + 1
            order.save(update_fields=["progress_remaining"])
        return result

    monkeypatch.setattr(services, "advance", corrupt)
    contract = effect_contract(checkout_order, examples=EXAMPLES)
    case = next(
        row.values[0] for row in contract_cases(contract) if row.id == "REPLAY_SAFE_EXECUTION-assert_replay_converges"
    )
    with pytest.raises(AssertionError, match="declared product outcome"):
        case.run()


def test_process_contract_rejects_disabled_recovery(monkeypatch):
    monkeypatch.setattr(type(checkout_order), "recover", lambda self: 0)
    with pytest.raises(HistoriesDiverged, match="after payment receipt"):
        assert_process_deaths_converge(EXAMPLES.process_handoffs[0])


def test_process_contract_rejects_missing_stock_compensation(monkeypatch):
    original = services.advance

    def corrupt(key, stage):
        before = Order.objects.get(submission_key=key)
        result = original(key, stage)
        if before.reserved and Order.objects.get(pk=before.pk).status == Order.Status.CANCELLED:
            # Fault injection: undo the real refund, leaving a lost widget.
            Product.objects.filter(pk=before.product_id).update(inventory=99)
        return result

    monkeypatch.setattr(services, "advance", corrupt)
    with pytest.raises(HistoriesDiverged, match="after reservation"):
        assert_process_deaths_converge(EXAMPLES.process_handoffs[1])
