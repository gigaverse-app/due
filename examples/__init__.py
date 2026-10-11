"""Select the same Celery application in Django commands, web, worker and Beat."""

from examples.celery_app import app as celery_app

__all__ = ["celery_app"]
