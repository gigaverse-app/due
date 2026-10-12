"""Install only the capabilities a standalone test actually exercises."""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager

import pytest

from due.django.models import UniverseDurableWork
from due.django.runtime.registry import (
    DurableWorkRegistration,
    index_durable_work_registrations,
    installed,
    registration_for,
)
from due.django.runtime.tasks import capability_for


def effective_registrations(registrations: Iterable[DurableWorkRegistration]) -> list[DurableWorkRegistration]:
    """Resolve test overlays in order: later route names or models replace earlier ones.

    This only selects registrations; the canonical runtime index still validates
    their queues, cadences and deadlines. Application adapters can use the same
    policy while retaining their live manifest mapping's identity.
    """
    by_model: dict[type[UniverseDurableWork], DurableWorkRegistration] = {}
    for entry in registrations:
        by_model = {model: kept for model, kept in by_model.items() if kept.name != entry.name}
        by_model[entry.model] = entry
    return list(by_model.values())


@contextmanager
def durable_work_installed(*extra: DurableWorkRegistration) -> Iterator[None]:
    previous = installed.value or {}
    effective = effective_registrations([*previous.values(), *extra])
    with installed.installed(index_durable_work_registrations(effective)):
        yield


registered_capability = capability_for
registration_of = registration_for


# Applications provide the iterable fixture from their own manifest. Requiring
# it prevents an empty installation from masquerading as production wiring.


@pytest.fixture(name="installed_durable_work")
def installed_durable_work_fixture(durable_work_registrations: Iterable[DurableWorkRegistration]) -> Iterator[None]:
    with durable_work_installed(*durable_work_registrations):
        yield
