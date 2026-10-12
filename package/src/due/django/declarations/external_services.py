"""Typed collaborators cached for one admitted execution, never on a capability.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import cast

from django.db import connection


@dataclass
class _ExecutionExternalServices:
    # This heterogeneous cache is keyed by the typed factory object itself.
    values: dict[object, object] = field(default_factory=dict)
    active: bool = True


_current: ContextVar[_ExecutionExternalServices | None] = ContextVar("durable_work_external_services", default=None)


@contextmanager
def execution_external_services() -> Iterator[None]:
    """The runner opens a scope only after admission and universe selection."""
    scope = _ExecutionExternalServices()
    token = _current.set(scope)
    try:
        yield
    finally:
        scope.active = False
        scope.values.clear()
        _current.reset(token)


@dataclass(frozen=True, eq=False)
class ExternalService[ServiceT]:
    """A typed factory declaration; the returned service owns its resource lifetime."""

    factory: Callable[[], ServiceT]

    def __call__(self) -> ServiceT:
        scope = _current.get()
        if scope is not None and not scope.active:
            raise RuntimeError("external_service() cannot reuse an expired durable execution")
        if scope is not None and self in scope.values:
            return cast(ServiceT, scope.values[self])
        if connection.in_atomic_block or not connection.get_autocommit():
            raise RuntimeError("resolve external_service() before entering a settlement transaction")
        if scope is None:
            return self.factory()
        scope.values[self] = self.factory()
        # Each declaration's unique key admits only results of its own factory.
        return cast(ServiceT, scope.values[self])
