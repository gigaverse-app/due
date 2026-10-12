"""Standalone host setup for runtime and conformance controls."""

from collections.abc import Iterator

import pytest
from celery import Celery
from celery.contrib.testing.app import setup_default_app
from pytest_obligation import configure

from due.django.conformance.host import work_table_host

configure(work_table_host(production_packages={"tests.harness"}))


@pytest.fixture(scope="session", autouse=True)
def library_celery_app() -> Iterator[None]:
    with Celery("due_library_tests", set_as_current=False) as app, setup_default_app(app):
        app.config_from_object("django.conf:settings", namespace="CELERY")
        app.conf.imports = ("due.django.runtime.tasks",)
        app.set_default()
        app.set_current()
        yield
