"""External index recording shared by contract and application journey tests."""

import os
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from examples.books import search


@dataclass
class IndexCalls:
    writes: Counter[str] = field(default_factory=Counter)
    publications: Counter[str] = field(default_factory=Counter)


calls: ContextVar[IndexCalls] = ContextVar("book_index_calls")


@contextmanager
def index_environment() -> Iterator[IndexCalls]:
    observed = IndexCalls()
    put, publish = search.put, search.publish

    def write(entry: search.IndexEntry) -> str:
        observed.writes[entry.key] += 1
        return put(entry)

    def activate(key: str) -> str:
        observed.publications[key] += 1
        return publish(key)

    token = calls.set(observed)
    try:
        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, {"DUE_BOOK_INDEX": str(Path(directory) / "index.sqlite3")}),
            patch.object(search, "put", write),
            patch.object(search, "publish", activate),
        ):
            yield observed
    finally:
        calls.reset(token)
