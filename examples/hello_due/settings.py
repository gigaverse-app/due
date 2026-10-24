"""Only the task backend and tutorial app, using the shared development services."""

import os

from examples.settings import DATABASES as DATABASES

SECRET_KEY = "local-hello-due-only"
USE_TZ = True
INSTALLED_APPS = ["due.django", "due.django.tasks", "examples.hello_due.apps.HelloConfig"]
CELERY_BROKER_URL = os.environ.get("DUE_BROKER_URL", "redis://127.0.0.1:56389/0")
CELERY_TASK_DEFAULT_QUEUE = os.environ.get("DUE_QUEUE", "hello-due")
TASKS = {
    "default": {
        "BACKEND": "due.django.tasks.backend.DueBackend",
        "QUEUES": [CELERY_TASK_DEFAULT_QUEUE],
        "OPTIONS": {
            "replay_safe_tasks": ["examples.hello_due.tasks.greet"],
            "lease_duration": 20,
            "retry_delay": 1,
        },
    }
}
