"""Tests exercise the same settings and migrations as the runnable example."""

from examples.settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "django_celery_beat"]  # noqa: F405

CELERY_IMPORTS = ("due.django.runtime.tasks",)
