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
    if mutation == "double dispatch":
        original_apply = type(checkout_order).apply

        def duplicate(self, work, context):
            final = Order.objects.get(pk=work.owner_id).progress_remaining == 1
            result = original_apply(self, work, context)
            if final:
                original_apply(self, work, context)
            return result

        monkeypatch.setattr(type(checkout_order), "apply", duplicate)
    else:
        original = services.advance

        def corrupt(work):
            result = original(work)
            order = Order.objects.get(pk=work.owner_id)
            if order.status == Order.Status.DISPATCHED:
                order.progress_remaining = 1
                order.save(update_fields=["progress_remaining"])
            return result

        monkeypatch.setattr(services, "advance", corrupt)
    contract = effect_contract(checkout_order, examples=EXAMPLES)
    case = next(
        row.values[0]
        for row in contract_cases(contract)
        if row.id == "delivery-replay-final dispatch-assert_delivery_replay"
    )
    with pytest.raises(AssertionError, match="declared product outcome|repeated or skipped its callback"):
        case.run()


@pytest.mark.parametrize("history", [0, 2], ids=["with hints", "without hints"])
def test_process_contract_rejects_disabled_recovery(monkeypatch, history):
    monkeypatch.setattr(type(checkout_order), "recover", lambda self: 0)
    with pytest.raises(HistoriesDiverged, match="after payment receipt"):
        assert_process_deaths_converge(EXAMPLES.process_handoffs[history])


@pytest.mark.parametrize("inventory", [99, 101], ids=["missing refund", "double refund"])
def test_process_contract_rejects_wrong_stock_compensation(monkeypatch: pytest.MonkeyPatch, inventory: int) -> None:
    original = services.advance

    def corrupt(work):
        before = Order.objects.get(pk=work.owner_id)
        result = original(work)
        if before.reserved and Order.objects.get(pk=before.pk).status == Order.Status.CANCELLED:
            # Fault injection: corrupt the actual refund, losing or duplicating stock.
            Product.objects.filter(pk=before.product_id).update(inventory=inventory)
        return result

    monkeypatch.setattr(services, "advance", corrupt)
    with pytest.raises(HistoriesDiverged, match="after reservation"):
        assert_process_deaths_converge(EXAMPLES.process_handoffs[1])


def test_generated_gate_rejects_selection_before_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    from examples.widget_store.models import CheckoutWork

    monkeypatch.setattr(type(checkout_order), "due_work", lambda self: CheckoutWork.objects.all())
    contract = effect_contract(checkout_order, examples=EXAMPLES)
    case = next(
        row.values[0]
        for row in contract_cases(contract)
        if row.id == "eligibility-payment received-assert_blocked_gate_preserves_intent"
    )
    with pytest.raises(AssertionError, match="blocked intent was selected"):
        case.run()


def test_process_contract_rejects_dropped_continuation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type(checkout_order), "transition", lambda self, work: None)
    with pytest.raises(HistoriesDiverged, match="after payment receipt"):
        assert_process_deaths_converge(EXAMPLES.process_handoffs[0])


@pytest.mark.parametrize("fault", ["early", "stalled", "reversed"])
def test_generated_completion_rejects_broken_progress(monkeypatch, fault):
    from django.test import override_settings

    original = services.advance

    def corrupt(work):
        original(work)
        assert work.owner is not None
        if work.owner.progress_remaining == 5:
            work.owner.progress_remaining = 9
            work.owner.save(update_fields=["progress_remaining"])

    if fault == "stalled":
        monkeypatch.setattr(type(checkout_order), "recover", lambda self: 0)
    elif fault == "reversed":
        monkeypatch.setattr(services, "advance", corrupt)
    else:
        transition = type(checkout_order).transition

        def early(self, work):
            with override_settings(WIDGET_STAGE_SECONDS=0.001):
                return transition(self, work)

        monkeypatch.setattr(type(checkout_order), "transition", early)
    case = next(
        row.values[0]
        for row in contract_cases(effect_contract(checkout_order, examples=EXAMPLES))
        if row.id == "completion-paid hints-assert_completes_within"
    )
    with pytest.raises(AssertionError, match="time bound|minimum wait|reversed"):
        case.run()
