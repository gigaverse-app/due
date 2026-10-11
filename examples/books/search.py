"""A local external index; PostgreSQL still owns books and their obligations.

SQLite is a standalone provider for this demo, shared by web and worker on one
machine. Its transactions never participate in Due's PostgreSQL transactions.
"""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel


class IndexEntry(BaseModel):
    """The search provider's wire/storage contract, derived from captured inputs."""

    key: str
    title: str
    author: str
    published: bool = False


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    path = Path(os.environ.get("DUE_BOOK_INDEX", "/tmp/due-books/index.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS books (key TEXT PRIMARY KEY, title TEXT, author TEXT, published INTEGER DEFAULT 0)"
            )
            yield conn
    finally:
        conn.close()


def put(entry: IndexEntry) -> str:
    # One immutable admission/revision key deduplicates writes after a worker
    # dies between this provider commit and Due's PostgreSQL settlement.
    with connection() as conn:
        conn.execute(
            "INSERT INTO books (key, title, author) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET title=excluded.title, author=excluded.author",
            (entry.key, entry.title, entry.author),
        )
    return entry.key


def get(key: str) -> IndexEntry | None:
    with connection() as conn:
        row = conn.execute("SELECT key, title, author, published FROM books WHERE key = ?", (key,)).fetchone()
    return IndexEntry(key=row[0], title=row[1], author=row[2], published=bool(row[3])) if row else None


def publish(key: str) -> str:
    # Activation is idempotent, including a crash before the Django pointer commits.
    with connection() as conn:
        if conn.execute("UPDATE books SET published = 1 WHERE key = ?", (key,)).rowcount != 1:
            raise LookupError("The indexed book is not readable yet")
    return key


def search(query: str) -> list[str]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT key FROM books WHERE published = 1 AND "
            "(instr(lower(title), lower(?)) OR instr(lower(author), lower(?))) ORDER BY key",
            (query, query),
        ).fetchall()
    return [row[0] for row in rows]
