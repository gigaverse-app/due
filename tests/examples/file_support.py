"""Real file provider scope shared by document and scheduled application proofs."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from due.django.conformance.provider import ProviderProbe
from examples.documents import storage


@dataclass(frozen=True)
class Files:
    root: Path
    writes: ProviderProbe[[str, str], str, str]


files: ContextVar[Files] = ContextVar("application_file_provider")


@contextmanager
def file_environment() -> Iterator[Files]:
    with TemporaryDirectory() as directory:
        observed = Files(Path(directory), ProviderProbe(storage.write_revision, key=lambda text, key: key))
        token = files.set(observed)
        try:
            with (
                patch.dict(os.environ, {"DUE_OUTPUT_DIR": directory}),
                observed.writes.patch(storage, "write_revision"),
            ):
                yield observed
        finally:
            files.reset(token)
