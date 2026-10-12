"""Shared test-owned work and provider for receipt and callback histories."""

from collections.abc import Iterator
from typing import ClassVar

import pytest
from django.db import connection, models
from django.test.utils import isolate_apps

from due.django.capabilities.scheduled import ScheduledWork
from tests.harness.primitive import PrimitiveOwner

with isolate_apps("tests"):

    class EvidenceWork(ScheduledWork):
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(PrimitiveOwner, on_delete=models.SET_NULL, null=True)

        class Meta:
            app_label = "tests"


class Provider:
    failure: ClassVar[type[BaseException] | None] = None

    @classmethod
    def write(cls) -> None:
        if cls.failure is not None:
            raise cls.failure("provider interrupted")


@pytest.fixture
def evidence_tables(isolated_primitive: None) -> Iterator[None]:
    with connection.schema_editor() as editor:
        editor.create_model(EvidenceWork)
    try:
        yield
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(EvidenceWork)
