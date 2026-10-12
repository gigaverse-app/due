"""Run demo children against pytest's actual database and verify requested death."""

import os
import subprocess
import sys
from pathlib import Path

from django.db import connection
from pytest_obligation.process_histories import fault_environment, fault_happened


def run_child(module: str, argument: str, point: str | None, marker: Path) -> int:
    base = os.environ.get("DUE_DATABASE_URL", "postgresql://due:due@127.0.0.1:55439/due")
    result = subprocess.run(
        [sys.executable, "-m", module, argument],
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "examples.settings",
            "DUE_DATABASE_URL": base.rsplit("/", 1)[0] + "/" + str(connection.settings_dict["NAME"]),
            **fault_environment(point, marker),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if point is not None:
        assert fault_happened(marker), result.stderr
        assert result.returncode != 0, result.stderr
    else:
        assert result.returncode == 0, result.stderr
    return result.returncode
