"""Immutable file provider shared by the Due app and its Celery comparison."""

import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def revision_key(operation_key: str) -> str:
    return hashlib.sha256(operation_key.encode()).hexdigest()


def _matches(destination: Path, content: bytes) -> bool:
    try:
        existing = destination.read_bytes()
    except FileNotFoundError:
        return False
    if existing != content:
        raise ValueError("operation key already has different content")
    return True


def write_revision(text: str, operation_key: str) -> str:
    """Publish complete bytes once; equal retries read, conflicting retries fail.

    A same-filesystem hard link atomically installs the completed file without
    replacing a winner. Process death before linking can leave a hidden staging
    directory, but never a partially visible revision. This local demo provider
    assumes its directory is exclusively owned; it does not promise power-loss
    durability or reclaim staging files left by killed writers.
    """
    key = revision_key(operation_key)
    output = Path(os.environ.get("DUE_OUTPUT_DIR", "/tmp/due-documents"))
    destination = output / key
    content = text.encode("utf-8")
    if _matches(destination, content):
        return key
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".pending-", dir=output) as staging:
        pending = Path(staging) / "content"
        pending.write_bytes(content)
        try:
            os.link(pending, destination)
        except FileExistsError:
            # Another writer won between the initial read and atomic install.
            if not _matches(destination, content):
                raise FileNotFoundError("published revision disappeared during a write") from None
    return key
