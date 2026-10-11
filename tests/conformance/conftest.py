from collections.abc import Iterator

import pytest
from pytest_obligation import configure

from due.django.conformance.host import work_table_host
from examples.celery_app import app
from tests.conformance.document_examples import environment

configure(work_table_host(production_packages={"examples"}))
app.set_default()
app.set_current()


@pytest.fixture
def document_files() -> Iterator[None]:
    with environment():
        yield
