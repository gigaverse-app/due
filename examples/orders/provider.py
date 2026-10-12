"""An independent local printer; every submission creates another physical order.

The reference is searchable correlation, deliberately NOT an idempotency key.
Use DUE_PRINTER_LOSE_RESPONSE=1 and DUE_PRINTER_HIDE_RECEIPTS=1 to demonstrate
accepted-but-unknown outcomes and temporarily unavailable evidence.
"""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import BaseModel

from examples.books.search import connection as index_connection


class PrintReceipt(BaseModel):
    order_id: int
    reference: str
    title: str


class PrinterRejected(Exception):
    """Provider positively rejected the request before accepting an order."""


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    with index_connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS print_orders (id INTEGER PRIMARY KEY, reference TEXT, title TEXT)")
        yield conn


def submit(reference: str, title: str) -> PrintReceipt:
    if os.environ.get("DUE_PRINTER_REJECT") == "1":
        raise PrinterRejected("Printer rejected before accepting an order")
    with connection() as conn:
        cursor = conn.execute("INSERT INTO print_orders (reference, title) VALUES (?, ?)", (reference, title))
        assert cursor.lastrowid is not None
        receipt = PrintReceipt(order_id=cursor.lastrowid, reference=reference, title=title)
    # The remote transaction has committed. Raising here must never permit an
    # automatic resend: doing so would create a second order.
    if os.environ.get("DUE_PRINTER_LOSE_RESPONSE") == "1":
        raise TimeoutError("Printer accepted the order but its response was lost")
    return receipt


def observe(reference: str) -> PrintReceipt | None:
    if os.environ.get("DUE_PRINTER_HIDE_RECEIPTS") == "1":
        return None
    return receipt(reference)


def receipt(reference: str) -> PrintReceipt | None:
    """Inspect the printer ledger directly, independently of observation availability."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, reference, title FROM print_orders WHERE reference = ?", (reference,)
        ).fetchall()
    if len(rows) > 1:
        raise ValueError("Multiple printer orders share a reference; operator reconciliation required")
    return PrintReceipt(order_id=rows[0][0], reference=rows[0][1], title=rows[0][2]) if rows else None
