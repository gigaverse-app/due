from django.apps import AppConfig


class ExamplesConfig(AppConfig):
    name = "examples"

    def ready(self) -> None:
        from due.django.runtime.registry import configure_durable_work
        from examples.registry import REGISTRATIONS

        configure_durable_work(REGISTRATIONS)
