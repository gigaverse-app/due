"""Standalone storefront: only its models, routes and recovery registration."""

from examples.settings import *  # noqa: F403

INSTALLED_APPS = ["due.django", "examples.widget_store.apps.WidgetStoreStandaloneConfig"]
ROOT_URLCONF = "examples.widget_store.urls"

# Reuse Django's supported per-process pool, as DBOS already does through
# SQLAlchemy. Task/request cleanup returns connections without reconnecting.
from examples.settings import DATABASES as BASE_DATABASES  # noqa: E402

DATABASES = {
    "default": {
        **BASE_DATABASES["default"],
        # Validate pooled sockets after database restarts/connection termination.
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"pool": {"min_size": 1, "max_size": 4, "timeout": 5}},
    }
}
# Celery prefork otherwise closes Django's entire pool before/after each task.
# Recycle after five tasks; recovery/fencing and transaction boundaries are unchanged.
CELERY_DB_REUSE_MAX = 5
