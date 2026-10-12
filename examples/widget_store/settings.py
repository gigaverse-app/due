"""Standalone storefront: only its models, routes and recovery registration."""

from examples.settings import *  # noqa: F403

INSTALLED_APPS = ["due.django", "examples.widget_store.apps.WidgetStoreStandaloneConfig"]
ROOT_URLCONF = "examples.widget_store.urls"
