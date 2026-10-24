from django.apps import AppConfig
from django.conf import settings


class AcmeConfig(AppConfig):
    name = "acme"

    def ready(self):
        from due.django.runtime.registry import (
            DurableWorkRegistration,
            configure_durable_work,
        )
        from due.django.tasks.runtime import django_tasks_registration

        from .effects import (
            cleanup_spool,
            order_printing,
            project_portal,
            publish_proof,
        )

        configure_durable_work(
            [
                django_tasks_registration(recovery_queue=settings.CELERY_TASK_DEFAULT_QUEUE, cadence_seconds=5),
                *[
                    DurableWorkRegistration(
                        name=name,
                        capability=capability,
                        queue=settings.CELERY_TASK_DEFAULT_QUEUE,
                        cadence_seconds=5,
                    )
                    for name, capability in [
                        ("acme_proof", publish_proof),
                        ("acme_portal", project_portal),
                        ("acme_print", order_printing),
                        ("acme_cleanup", cleanup_spool),
                    ]
                ],
            ]
        )
