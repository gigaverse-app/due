"""Start temporary local workers, run all stages, then stop owned processes."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], check=True)
processes = []
with (
    TemporaryDirectory(prefix="acme-beat-") as temporary,
    (ROOT / "worker.log").open("w") as worker_log,
    (ROOT / "beat.log").open("w") as beat_log,
):
    try:
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "project.celery:app",
                    "worker",
                    "--loglevel=INFO",
                    "--concurrency=2",
                    "--queues=" + os.getenv("ACME_QUEUE", "default"),
                    "--hostname=acme-" + uuid4().hex + "@%h",
                ],
                stdout=worker_log,
                stderr=subprocess.STDOUT,
            )
        )
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "project.celery:app",
                    "beat",
                    "--loglevel=INFO",
                    "--schedule=" + str(Path(temporary) / "schedule"),
                ],
                stdout=beat_log,
                stderr=subprocess.STDOUT,
            )
        )
        subprocess.run([sys.executable, "manage.py", "demo"], check=True)
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
