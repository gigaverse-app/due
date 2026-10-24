from django.apps import AppConfig
from django.conf import settings


class HelloConfig(AppConfig):
    name = "examples.hello_due"

    def ready(self) -> None:
        from due.django.public import configure_durable_work, django_tasks_registration

        from .celery import app

        # Short demo timings: the lease still outlives the hard execution limit.
        configure_durable_work(
            [
                django_tasks_registration(
                    recovery_queue=settings.CELERY_TASK_DEFAULT_QUEUE,
                    cadence_seconds=2,
                    execution_soft_time_limit=12,
                    execution_time_limit=15,
                )
            ],
            celery_app=app,
        )
