"""A web/management process must bind the same Celery app as a worker."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.no_db
def test_django_startup_configures_the_example_broker_and_queue() -> None:
    broker = "redis://127.0.0.1:56389/9"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
import django
django.setup()
from celery import current_app
print(json.dumps({
    "app": current_app.main,
    "broker": current_app.conf.broker_url,
    "queue": current_app.conf.task_default_queue,
    "imports": list(current_app.conf.imports),
}))
""",
        ],
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "examples.settings",
            "DUE_BROKER_URL": broker,
            "DUE_QUEUE": "startup-test",
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "app": "due_example",
        "broker": broker,
        "queue": "startup-test",
        "imports": ["due.django.runtime.tasks"],
    }
