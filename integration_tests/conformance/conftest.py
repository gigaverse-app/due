from collections.abc import Iterator

import pytest
from pytest_obligation import configure

from due.django.conformance.host import work_table_host
from examples.celery_app import app
from integration_tests.conformance.document_examples import environment
from integration_tests.examples.books_support import index_environment

configure(work_table_host(production_packages={"examples"}))
app.set_default()
app.set_current()


@pytest.fixture
def document_files() -> Iterator[None]:
    with environment():
        yield


@pytest.fixture
def book_index() -> Iterator[None]:
    with index_environment():
        yield


@pytest.fixture
def printer() -> Iterator[None]:
    from integration_tests.conformance.order_examples import environment as printer_environment

    with printer_environment():
        yield


@pytest.fixture
def scheduled_files() -> Iterator[None]:
    from integration_tests.conformance.scheduled_examples import environment as scheduled_environment

    with scheduled_environment():
        yield
