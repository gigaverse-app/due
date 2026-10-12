"""Provider identity protects committed bytes even when a retry is interrupted."""

from pathlib import Path

import pytest

from examples.documents.storage import revision_key, write_revision


@pytest.fixture(autouse=True)
def output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUE_OUTPUT_DIR", str(tmp_path))


def test_same_operation_replay_does_not_rewrite_published_bytes(tmp_path: Path) -> None:
    key = write_revision("Published edition", "revision-1")
    path = tmp_path / key
    before = path.stat()
    assert write_revision("Published edition", "revision-1") == key
    after = path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert path.read_text() == "Published edition"


def test_conflicting_payload_cannot_replace_an_immutable_revision(tmp_path: Path) -> None:
    write_revision("Published edition", "revision-1")
    with pytest.raises(ValueError, match="different content"):
        write_revision("Wrong edition", "revision-1")
    assert (tmp_path / revision_key("revision-1")).read_text() == "Published edition"


@pytest.mark.parametrize("conflict", [False, True])
def test_concurrent_writers_preserve_one_complete_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict: bool
) -> None:
    import os
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    link = os.link

    def install(source: Path, destination: Path) -> None:
        barrier.wait(timeout=10)
        link(source, destination)

    monkeypatch.setattr(os, "link", install)
    contents = ["First edition" * 1000, ("Other edition" if conflict else "First edition") * 1000]

    def write(text: str) -> str | ValueError:
        try:
            return write_revision(text, "racing-revision")
        except ValueError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as writers:
        results = list(writers.map(write, contents))
    assert sum(isinstance(result, ValueError) for result in results) == int(conflict)
    assert (tmp_path / revision_key("racing-revision")).read_text() in contents
    assert len(list(tmp_path.iterdir())) == 1


def test_death_during_first_write_exposes_no_partial_revision_and_retry_recovers(tmp_path: Path) -> None:
    import subprocess
    import sys

    child = """
import os
from pathlib import Path
from unittest.mock import patch
from examples.documents.storage import write_revision
original = Path.write_bytes

def interrupted(path, content):
    original(path, content[:3])
    os._exit(73)

with patch.object(Path, "write_bytes", interrupted):
    write_revision("Complete edition", "crashed-revision")
"""
    result = subprocess.run([sys.executable, "-c", child], timeout=20)
    assert result.returncode == 73
    assert not (tmp_path / revision_key("crashed-revision")).exists()
    key = write_revision("Complete edition", "crashed-revision")
    assert (tmp_path / key).read_text() == "Complete edition"
