import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "examples.settings")

app = Celery("due_example", broker=os.environ.get("DUE_BROKER_URL", "redis://127.0.0.1:56389/0"))
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.update(
    imports=("due.django.runtime.tasks",),
    task_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
)
