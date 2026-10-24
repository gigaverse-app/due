import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "local-acme-tutorial-only"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
INSTALLED_APPS = ["due.django", "due.django.tasks", "acme.apps.AcmeConfig"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("PGDATABASE", "acme"),
        "USER": os.getenv("PGUSER", "acme"),
        "PASSWORD": os.getenv("PGPASSWORD", "acme"),
        "HOST": os.getenv("PGHOST", "127.0.0.1"),
        "PORT": os.getenv("PGPORT", "55451"),
    }
}
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:56399/0")
CELERY_TASK_DEFAULT_QUEUE = os.getenv("ACME_QUEUE", "default")
PROVIDER_DB = Path(os.getenv("ACME_PROVIDER_DB", str(BASE_DIR / "providers.sqlite3")))
TASKS = {
    "default": {
        "BACKEND": "due.django.tasks.backend.DueBackend",
        "QUEUES": [CELERY_TASK_DEFAULT_QUEUE],
        "OPTIONS": {"replay_safe_tasks": ["acme.tasks.count_words"]},
    }
}
