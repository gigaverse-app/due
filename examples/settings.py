"""Isolated local/CI configuration; never reads a production credential."""

import os
from urllib.parse import unquote, urlparse

SECRET_KEY = "due-test-only"
INSTALLED_APPS = [
    "due.django",
    "examples.apps.ExamplesConfig",
    "examples.documents",
    "examples.books",
    "examples.catalog",
    "examples.orders",
    "examples.scheduled",
]
ROOT_URLCONF = "examples.urls"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
MIDDLEWARE = ["django.middleware.csrf.CsrfViewMiddleware"]
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True}]
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
CELERY_TASK_DEFAULT_QUEUE = os.environ.get("DUE_QUEUE", "due")
CELERY_BEAT_SCHEDULE = {}
