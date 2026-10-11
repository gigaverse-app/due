"""Install only the capabilities a standalone test actually exercises."""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager

import pytest

from due.django.runtime.registry import (
    DurableWorkRegistration,
    index_durable_work_registrations,
    installed,
    registration_for,
)
from due.django.runtime.tasks import capability_for


@contextmanager
def durable_work_installed(*extra: DurableWorkRegistration) -> Iterator[None]:
    previous = installed.value or {}
    by_model = {entry.model: entry for entry in previous.values()}
    for entry in extra:
        by_model = {model: kept for model, kept in by_model.items() if kept.name != entry.name}
        by_model[entry.model] = entry
    with installed.installed(index_durable_work_registrations(by_model.values())):
        yield


registered_capability = capability_for
registration_of = registration_for


# Applications provide the iterable fixture from their own manifest. Requiring
# it prevents an empty installation from masquerading as production wiring.


@pytest.fixture(name="installed_durable_work")
def installed_durable_work_fixture(durable_work_registrations: Iterable[DurableWorkRegistration]) -> Iterator[None]:
    with durable_work_installed(*durable_work_registrations):
        yield
