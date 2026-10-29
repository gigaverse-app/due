"""Explicit keyed resource lifetimes shared by nested synchronous executions."""

import asyncio
import os
import threading
from collections.abc import Callable, Hashable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


def _owner() -> tuple[int, int, asyncio.Task | None]:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


@dataclass
class _ResourceLease[KeyT: Hashable, ValueT]:
    key: KeyT
    value: ValueT
    owner: tuple[int, int, asyncio.Task | None]
    active: bool = True


@dataclass(frozen=True, eq=False)
class ExecutionResource[KeyT: Hashable, ValueT]:
    """Reuse a resource only inside an explicitly owned, matching-key scope.

    The factory owns acquisition and cleanup. Validation runs on every borrow,
    for example to reject a replaced database session holding an advisory lock.
    No work ownership, eligibility or mutable product snapshot is cached here.
    """

    factory: Callable[[KeyT], AbstractContextManager[ValueT]]
    validate: Callable[[ValueT], None] | None = None
    _leases: ContextVar[tuple[_ResourceLease[KeyT, ValueT], ...]] = field(
        default_factory=lambda: ContextVar("due_execution_resources", default=()), init=False, repr=False
    )

    @contextmanager
    def acquire(self, key: KeyT) -> Iterator[ValueT]:
        owner = _owner()
        for lease in reversed(self._leases.get()):
            # A copied context, another task, thread or fork cannot inherit the
            # right to use a resource after its owner has closed or moved on.
            if lease.active and lease.owner == owner and lease.key == key:
                if self.validate is not None:
                    self.validate(lease.value)
                yield lease.value
                return
        with self.factory(key) as value:
            lease = _ResourceLease(key, value, owner)
            token = self._leases.set((*self._leases.get(), lease))
            try:
                if self.validate is not None:
                    self.validate(value)
                yield value
            finally:
                lease.active = False
                self._leases.reset(token)
