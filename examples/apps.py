from django.apps import AppConfig


class ExamplesConfig(AppConfig):
    name = "examples"

    def ready(self) -> None:
        from django.conf import settings

        from due.django.runtime.registry import configure_durable_work
        from examples.registry import REGISTRATIONS

        names = getattr(settings, "DUE_EXAMPLE_CAPABILITIES", None)
        configure_durable_work(tuple(entry for entry in REGISTRATIONS if names is None or entry.name in names))
