"""Replay-safe file provider shared by the Due app and its Celery comparison."""

import hashlib
import os
from pathlib import Path


def revision_key(operation_key: str) -> str:
    return hashlib.sha256(operation_key.encode()).hexdigest()


def write_revision(text: str, operation_key: str) -> str:
    key = revision_key(operation_key)
    output = Path(os.environ.get("DUE_OUTPUT_DIR", "/tmp/due-documents"))
    output.mkdir(parents=True, exist_ok=True)
    (output / key).write_text(text)
    return key
