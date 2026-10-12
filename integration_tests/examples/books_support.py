"""External index recording shared by contract and application journey tests."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from due.django.conformance.provider import ProviderProbe
from examples.books import search


@dataclass
class IndexCalls:
    writes: ProviderProbe[[search.IndexEntry], str, str]
    publications: ProviderProbe[[str], str, str]


calls: ContextVar[IndexCalls] = ContextVar("book_index_calls")


@contextmanager
def index_environment() -> Iterator[IndexCalls]:
    observed = IndexCalls(
        writes=ProviderProbe(search.put, key=lambda entry: entry.key),
        publications=ProviderProbe(search.publish, key=lambda key: key),
    )
    token = calls.set(observed)
    try:
        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, {"DUE_BOOK_INDEX": str(Path(directory) / "index.sqlite3")}),
            observed.writes.patch(search, "put"),
            observed.publications.patch(search, "publish"),
        ):
            yield observed
    finally:
        calls.reset(token)
