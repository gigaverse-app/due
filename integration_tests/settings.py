"""Tests exercise the same settings and migrations as the runnable example."""

from examples.settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "django_celery_beat", "procrastinate.contrib.django"]  # noqa: F405

PROCRASTINATE_IMPORT_PATHS = ["examples.comparison.tasks"]
