"""Run the getting-started journey with its own Django settings and real processes."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import connection


@pytest.mark.django_db(transaction=True)
def test_tutorial_runs_and_recovers_after_a_real_worker_kill(tmp_path: Path) -> None:
    base = os.environ.get("DUE_DATABASE_URL", "postgresql://due:due@127.0.0.1:55439/due")
    result = subprocess.run(
        [sys.executable, "-m", "integration_tests.harness.hello_due_journey", str(tmp_path)],
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "examples.hello_due.settings",
            "DUE_DATABASE_URL": base.rsplit("/", 1)[0] + "/" + str(connection.settings_dict["NAME"]),
            "DUE_QUEUE": "hello-test-" + uuid4().hex,
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tutorial journey passed" in result.stdout
