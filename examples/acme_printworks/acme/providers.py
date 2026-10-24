"""Local provider simulators, each call commits independently of Django.

Proofs are text previews. Portal PUT is unconditional. Every printer POST
creates an order; reference is searchable but is NOT an idempotency key.
The renderer commits output and its final completion marker atomically.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

from django.conf import settings


@contextmanager
def database():
    db = sqlite3.connect(settings.PROVIDER_DB, timeout=30)
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS proofs (key TEXT PRIMARY KEY, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS portal (job_id INTEGER PRIMARY KEY, proof_key TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, reference TEXT, proof_key TEXT, quantity INTEGER);
            CREATE TABLE IF NOT EXISTS renders (key TEXT PRIMARY KEY, finished INTEGER, spool TEXT);
            CREATE TABLE IF NOT EXISTS faults (name TEXT PRIMARY KEY);
        """)
        with db:
            yield db
    finally:
        db.close()


def write_proof(text, proof_key):
    with database() as db:
        row = db.execute("SELECT text FROM proofs WHERE key=?", (proof_key,)).fetchone()
        if row is not None and row[0] != text:
            raise ValueError("Immutable proof key reused for different copy")
        db.execute("INSERT OR IGNORE INTO proofs VALUES (?,?)", (proof_key, text))
    return proof_key


def render(text, proof_key, spool_key):
    write_proof(text, proof_key)
    with database() as db:
        # A completed render cannot write again, even after cleanup.
        db.execute("INSERT OR IGNORE INTO renders VALUES (?,1,?)", (spool_key, text))
    return proof_key


def proof_text(key):
    with database() as db:
        return db.execute("SELECT text FROM proofs WHERE key=?", (key,)).fetchone()[0]


def put_portal(job_id, proof_key):
    with database() as db:
        db.execute(
            "INSERT INTO portal VALUES (?,?) ON CONFLICT(job_id) DO UPDATE SET proof_key=excluded.proof_key",
            (job_id, proof_key),
        )


def portal_key(job_id):
    with database() as db:
        row = db.execute("SELECT proof_key FROM portal WHERE job_id=?", (job_id,)).fetchone()
        return row[0] if row else None


@dataclass(frozen=True)
class PrintReceipt:
    order_id: int
    reference: str
    proof_key: str
    quantity: int


def lose_next_print_reply():
    with database() as db:
        db.execute("INSERT OR IGNORE INTO faults VALUES ('print_reply')")


def submit_print(reference, proof_key, quantity):
    with database() as db:
        cursor = db.execute(
            "INSERT INTO orders(reference,proof_key,quantity) VALUES (?,?,?)",
            (reference, proof_key, quantity),
        )
        assert cursor.lastrowid is not None
        receipt = PrintReceipt(cursor.lastrowid, reference, proof_key, quantity)
        lost = db.execute("DELETE FROM faults WHERE name='print_reply'").rowcount
    # The provider has COMMITTED. Rolling back Django cannot undo this order.
    if lost:
        raise TimeoutError("Printer accepted the order; response was lost")
    return receipt


receipts_visible = True


class PrinterRejected(Exception):
    """Provider explicitly guarantees this request created no order."""


def observe_print(reference):
    if not receipts_visible:
        return None
    return print_receipt(reference)


def print_receipt(reference):
    with database() as db:
        rows = db.execute(
            "SELECT id,reference,proof_key,quantity FROM orders WHERE reference=?",
            (reference,),
        ).fetchall()
        if len(rows) > 1:
            raise ValueError("Multiple orders share this reference; manual reconciliation required")
        return PrintReceipt(*rows[0]) if rows else None


def print_count(reference):
    with database() as db:
        return db.execute("SELECT count(*) FROM orders WHERE reference=?", (reference,)).fetchone()[0]


def cleanup(spool_key):
    with database() as db:
        row = db.execute("SELECT finished FROM renders WHERE key=?", (spool_key,)).fetchone()
        if row is None:
            return False  # Absence is not proof that a delayed write cannot arrive.
        db.execute("UPDATE renders SET spool=NULL WHERE key=?", (spool_key,))
    return bool(row[0])


def put_spool(key, text, *, finished):
    """Renderer simulator: a pending attempt can still emit delayed temporary bytes."""
    with database() as db:
        row = db.execute("SELECT finished FROM renders WHERE key=?", (key,)).fetchone()
        if row and row[0]:
            raise ValueError("A completed attempt cannot write more spool data")
        db.execute(
            "INSERT INTO renders VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET finished=excluded.finished,spool=excluded.spool",
            (key, int(finished), text),
        )
