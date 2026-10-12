"""Bounded subprocess lifetimes and failure logs for live application tests."""

import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Process:
    handle: subprocess.Popen[str]
    log: Path

    def wait_until(self, ready: Callable[[], bool], *, timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            assert self.handle.poll() is None, self.log.read_text()
            if ready():
                return
            time.sleep(0.1)
        raise AssertionError(f"Timed out waiting for {self.log.name}:\n{self.log.read_text()}")


@contextmanager
def running_process(
    directory: Path, name: str, command: list[str], env: dict[str, str], *, cwd: Path | None = None
) -> Iterator[Process]:
    log_path = directory / f"{name}.log"
    with log_path.open("w+") as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, text=True, cwd=cwd)
        try:
            yield Process(process, log_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
