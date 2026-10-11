from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    name = "examples.documents"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from django.conf import settings

        from due.django.runtime.registry import (
            DurableWorkRegistration,
            index_durable_work_registrations,
            install_registrations,
            recovery_schedules,
        )
        from examples.documents.effects import publish_document

        routes = [DurableWorkRegistration(name="publish_document", capability=publish_document, cadence_seconds=5)]
        install_registrations(index_durable_work_registrations(routes))
        settings.CELERY_BEAT_SCHEDULE = recovery_schedules(routes)
