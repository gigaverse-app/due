import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "examples.hello_due.settings")
app = Celery("hello_due")
app.config_from_object("django.conf:settings", namespace="CELERY")
