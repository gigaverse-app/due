from django.apps import AppConfig


class WidgetStoreStandaloneConfig(AppConfig):
    """Explicit standalone entry; the all-examples host owns its own manifest."""

    name = "examples.widget_store"
    default = False

    def ready(self) -> None:
        from due.django.runtime.registry import configure_durable_work
        from examples.widget_store.registration import REGISTRATION

        configure_durable_work((REGISTRATION,))
