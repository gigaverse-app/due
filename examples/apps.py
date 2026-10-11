from django.apps import AppConfig


class ExamplesConfig(AppConfig):
    name = "examples"

    def ready(self) -> None:
        from django.conf import settings

        from due.django.runtime.registry import (
            index_durable_work_registrations,
            install_registrations,
            recovery_schedules,
        )
        from examples.registry import REGISTRATIONS

        install_registrations(index_durable_work_registrations(REGISTRATIONS))
        settings.CELERY_BEAT_SCHEDULE.update(recovery_schedules(REGISTRATIONS))
