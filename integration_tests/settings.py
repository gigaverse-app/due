"""Tests exercise the same settings and migrations as the runnable example."""

import os

from examples.settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "django_celery_beat", "procrastinate.contrib.django"]  # noqa: F405

PROCRASTINATE_IMPORT_PATHS = ["examples.comparison.tasks"]

# Each pytest-xdist worker publishes to a queue of its own, so a real Celery worker started by one test consumes
# only that test's messages. DUE_QUEUE still wins, for tests that start a worker on a queue they name.
CELERY_TASK_DEFAULT_QUEUE = os.environ.get("DUE_QUEUE") or "due-" + os.environ.get("PYTEST_XDIST_WORKER", "main")
