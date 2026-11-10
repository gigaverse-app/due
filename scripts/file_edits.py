"""Guarded edits shared by disposable test projects and source mutations."""

from pathlib import Path


def replace_once(path: Path, old: str, new: str, *, error_type: type[Exception] = RuntimeError) -> None:
    source = path.read_text()
    if source.count(old) != 1:
        raise error_type(f"{path} must contain {old!r} exactly once")
    path.write_text(source.replace(old, new))
