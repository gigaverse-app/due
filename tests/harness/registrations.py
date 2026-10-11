"""Install only the capabilities a standalone test actually exercises."""

from collections.abc import Iterator
from contextlib import contextmanager

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
