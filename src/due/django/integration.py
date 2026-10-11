"""Application hooks for tenant context and read-after-write routing.

Install once during application startup, before registering capabilities. The
standalone default keeps a context-local tenant identifier and uses the default
Django database. Applications with their own tenancy/routing install their
existing context manager and pin callback; Due imports no application code.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

_tenant: ContextVar[str] = ContextVar("due_tenant", default="")


@contextmanager
def _tenant_scope(tenant: str) -> Iterator[None]:
    token = _tenant.set(tenant)
    try:
        yield
    finally:
        _tenant.reset(token)


def current_tenant() -> str:
    return _tenant.get()


def _no_pin(user_id: int) -> None:
    """The default Django database needs no read-replica pin."""


@dataclass(frozen=True)
class ApplicationHooks:
    tenant_scope: Callable[[str], AbstractContextManager[None]] = _tenant_scope
    pin_after_write: Callable[[int], None] = _no_pin


_hooks = ApplicationHooks()


def configure(hooks: ApplicationHooks) -> None:
    """Configure process-wide integration before workers or request threads start."""
    global _hooks
    _hooks = hooks


@contextmanager
def current_universe_scope(tenant: str) -> Iterator[None]:
    # Retain the original work_universe() contract for source-compatible adoption.
    with _tenant_scope(tenant), _hooks.tenant_scope(tenant):
        yield


def pin_after_write(user_id: int) -> None:
    _hooks.pin_after_write(user_id)
