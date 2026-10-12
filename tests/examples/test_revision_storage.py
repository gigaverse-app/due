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
from contextlib import contextmanager
original = Path.open

class InterruptedFile:
    def __init__(self, file):
        self.file = file
    def write(self, content):
        self.file.write(content[:3])
        self.file.flush()
        os._exit(73)

@contextmanager
def interrupted(path, mode="r", *args, **kwargs):
    with original(path, mode, *args, **kwargs) as file:
        yield InterruptedFile(file) if any(flag in mode for flag in "wx") else file

with patch.object(Path, "open", interrupted):
    write_revision("Complete edition", "crashed-revision")
"""
    result = subprocess.run([sys.executable, "-c", child], timeout=20)
    assert result.returncode == 73
    assert not (tmp_path / revision_key("crashed-revision")).exists()
    key = write_revision("Complete edition", "crashed-revision")
    assert (tmp_path / key).read_text() == "Complete edition"


def test_first_publication_needs_no_per_revision_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The output directory already exists. No staging-directory lifecycle is
    # needed to atomically install one complete immutable file.
    import os

    mkdir = os.mkdir

    def existing_output_only(path: str | Path, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        assert Path(path) == tmp_path, "one publication must not allocate a staging directory"
        mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", existing_output_only)
    key = write_revision("Complete edition", "no-directory")
    assert (tmp_path / key).read_text() == "Complete edition"


def test_publication_preserves_normal_file_creation_permissions(tmp_path: Path) -> None:
    reference = tmp_path / "ordinary"
    reference.write_bytes(b"reference")
    key = write_revision("Complete edition", "permissions")
    assert (tmp_path / key).stat().st_mode & 0o777 == reference.stat().st_mode & 0o777


def test_staging_collision_never_removes_another_writers_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from uuid import UUID

    from examples.documents import storage

    identity = UUID(int=1)
    occupied = tmp_path / f".pending-{identity.hex}"
    occupied.write_bytes(b"other writer's unfinished content")
    monkeypatch.setattr(storage, "uuid4", lambda: identity)
    with pytest.raises(FileExistsError):
        write_revision("Complete edition", "collision")
    assert occupied.read_bytes() == b"other writer's unfinished content"
    assert not (tmp_path / revision_key("collision")).exists()


def test_failed_publication_cleans_staging_and_can_be_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    def unavailable(source: Path, destination: Path) -> None:
        raise OSError("publication unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", unavailable)
        with pytest.raises(OSError, match="publication unavailable"):
            write_revision("Complete edition", "failed-link")
    assert list(tmp_path.iterdir()) == []
    key = write_revision("Complete edition", "failed-link")
    assert (tmp_path / key).read_text() == "Complete edition"
