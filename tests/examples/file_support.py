"""Count provider requests, independently of whether an immutable retry writes bytes."""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from tempfile import TemporaryDirectory
from unittest.mock import patch

from due.django.conformance.provider import ProviderProbe
from examples.documents import storage

probe: ContextVar[ProviderProbe[[str, str], str, str]] = ContextVar("file_provider")


@contextmanager
def environment() -> Iterator[None]:
    writer: Callable[[str, str], str] = storage.write_revision
    writes = ProviderProbe(writer, key=lambda text, operation_key: operation_key)
    token = probe.set(writes)
    try:
        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, {"DUE_OUTPUT_DIR": directory}),
            writes.patch(storage, "write_revision"),
        ):
            yield
    finally:
        probe.reset(token)
