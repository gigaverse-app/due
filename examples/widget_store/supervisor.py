"""Local-only POSIX supervisor. Kill all owned application processes and restart.

Run: uv run python -m examples.widget_store.supervisor --port 8000
PostgreSQL/Redis are independent and deliberately remain alive during the crash.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path


def supervise(commands: Sequence[Sequence[str]], directory: Path) -> None:
    restart = False
    stopped = False

    def request_restart(signum: int, frame: object) -> None:
        nonlocal restart
        restart = True

    def request_stop(signum: int, frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGUSR1, request_restart)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    environment = {**os.environ, "DUE_WIDGET_SUPERVISOR": str(os.getpid())}
    children: list[subprocess.Popen[bytes]] = []

    def kill_children() -> None:
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for child in children:
            child.wait()
        children.clear()

    try:
        while not stopped:
            if not children:
                for command in commands:
                    children.append(subprocess.Popen(command, cwd=directory, env=environment, start_new_session=True))
                print(f"WIDGET_STARTED pids={[child.pid for child in children]}", flush=True)
            if restart or any(child.poll() is not None for child in children):
                print("WIDGET_CRASH restarting every application process", flush=True)
                kill_children()
                restart = False
                time.sleep(0.5)
            time.sleep(0.1)
    finally:
        kill_children()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "examples.widget_store.settings")
    python = sys.executable
    supervise(
        [
            [python, "-m", "django", "runserver", f"127.0.0.1:{args.port}", "--noreload"],
            [
                python,
                "-m",
                "celery",
                "-A",
                "examples.celery_app",
                "worker",
                "--concurrency=1",
                "--loglevel=INFO",
                "--without-gossip",
                "--without-mingle",
            ],
            [
                python,
                "-m",
                "celery",
                "-A",
                "examples.celery_app",
                "beat",
                "--loglevel=INFO",
                "--schedule",
                f"/tmp/due-widget-beat-{os.getpid()}",
            ],
        ],
        Path.cwd(),
    )


if __name__ == "__main__":
    main()
