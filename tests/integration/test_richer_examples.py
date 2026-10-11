from pathlib import Path

import pytest
from django.core.management import call_command

from due.django.testing import hold_nudges
from examples.books import search
from examples.catalog.models import CatalogEntry
from examples.orders import provider
from examples.orders.models import PrintOrder
from tests.integration.worker import running_worker

pytestmark = pytest.mark.django_db(transaction=True)


def test_catalog_command_reaches_real_worker_and_external_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with running_worker(tmp_path, monkeypatch) as worker:
        with hold_nudges():
            call_command("catalog_entry", "First", "Author")
        entry = CatalogEntry.objects.get()
        worker.recover("project_catalog")

        def projected(title: str) -> bool:
            entry.refresh_from_db()
            return entry.published_title == title

        worker.wait_until(lambda: projected("First"))
        with hold_nudges():
            call_command("catalog_entry", "Second", "Author", entry_id=entry.pk)
        worker.recover("project_catalog")
        worker.wait_until(lambda: projected("Second"))
        assert search.search("Second") == [str(entry.identity)]
        assert search.search("First") == []


def test_print_command_observes_lost_response_in_real_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUE_PRINTER_LOSE_RESPONSE", "1")
    with running_worker(tmp_path, monkeypatch) as worker:
        with hold_nudges():
            call_command("print_book", "Kindred")
        order = PrintOrder.objects.get()

        def status(expected: str) -> bool:
            order.refresh_from_db()
            return order.status == expected

        worker.recover("print_order")
        worker.wait_until(lambda: status("unknown"))
        receipt = provider.receipt(str(order.reference))
        assert receipt is not None
        worker.recover("print_order")
        worker.wait_until(lambda: status("printed"))
        assert order.provider_order_id == receipt.order_id
        with provider.connection() as conn:
            assert conn.execute("SELECT count(*) FROM print_orders").fetchone()[0] == 1
