from pathlib import Path

import pytest

from due.django.conformance.delivery import WorkTableDelivery
from due.django.testing import hold_nudges
from due.errors import EffectOutcomeUnknownError
from examples.orders import provider
from examples.orders.effects import print_order
from examples.orders.models import PrintOrder
from examples.orders.services import order_print, receive_printer_receipt

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("receipt_path", ["observation", "callback"])
def test_unknown_print_is_observed_without_creating_a_second_physical_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, receipt_path: str
) -> None:
    monkeypatch.setenv("DUE_BOOK_INDEX", str(tmp_path / "printer.sqlite3"))
    monkeypatch.setenv("DUE_PRINTER_LOSE_RESPONSE", "1")
    monkeypatch.setenv("DUE_PRINTER_HIDE_RECEIPTS", "1")
    with hold_nudges():
        work = order_print("Kindred")
        with pytest.raises(EffectOutcomeUnknownError):
            print_order.attempt_execution(work.pk)
    with WorkTableDelivery(print_order).session() as recovery:
        recovery.recover()
        assert PrintOrder.objects.get(pk=work.owner_id).status == "unknown"
        if receipt_path == "callback":
            receipt = provider.receipt(str(work.reference))
            assert receipt is not None
            assert receive_printer_receipt(receipt)
            assert receive_printer_receipt(receipt)  # Duplicate delivery retains the same evidence.
        else:
            monkeypatch.setenv("DUE_PRINTER_HIDE_RECEIPTS", "0")
        recovery.recover()
    order = PrintOrder.objects.get(pk=work.owner_id)
    receipt = provider.receipt(str(order.reference))
    assert receipt is not None
    assert order.status == "printed"
    assert order.provider_order_id == receipt.order_id
    with provider.connection() as conn:
        assert conn.execute("SELECT count(*) FROM print_orders").fetchone()[0] == 1


def test_provider_really_creates_duplicates_if_caller_blindly_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DUE_BOOK_INDEX", str(tmp_path / "printer.sqlite3"))
    first = provider.submit("same-reference", "Kindred")
    second = provider.submit("same-reference", "Kindred")
    assert first.order_id != second.order_id
    with pytest.raises(ValueError, match="Multiple printer orders"):
        provider.observe("same-reference")
