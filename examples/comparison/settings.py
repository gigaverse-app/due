"""Optional Procrastinate comparison; installed only by development dependencies."""

from examples.settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "procrastinate.contrib.django"]  # noqa: F405
PROCRASTINATE_IMPORT_PATHS = ["examples.comparison.tasks"]
