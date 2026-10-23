"""Standalone library controls: no application or demo imports."""

import os
from urllib.parse import unquote, urlparse

SECRET_KEY = "due-library-tests"
INSTALLED_APPS = ["due.django", "due.django.tasks", "django_celery_beat"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
_db = urlparse(os.environ.get("DUE_DATABASE_URL", "postgresql://due:due@127.0.0.1:55439/due"))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _db.path.lstrip("/"),
        "USER": unquote(_db.username or ""),
        "PASSWORD": unquote(_db.password or ""),
        "HOST": _db.hostname,
        "PORT": _db.port or 5432,
    }
}
CELERY_TASK_DEFAULT_QUEUE = "due"
CELERY_BEAT_SCHEDULE = {}
