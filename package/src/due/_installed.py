"""
A value the composition root installs at process start.

A library that has to reach application code at run time cannot import it: every importer of the library,
in every process and in the test selector's graph, would then depend on the application. The library
declares an ``Installed`` slot instead, ``the application startup hook`` installs the application's value, and
reading the slot in a process that never called ``wire()`` raises rather than silently doing nothing.
Tests that exercise the real value install it for a block with ``slot.installed(value)``. Unlike a model hook,
an unwired slot raises in the test session too, and callers resolve it outside any best-effort ``except``
so the error is not logged as a routine failure (project-conventions L8).
"""

import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class NotInstalledError(RuntimeError):
    """A process used something ``the application startup hook`` installs, without having called it."""


class Installed[T]:
    """One installed value, read with ``get()``; ``value`` is ``None`` until ``install()``."""

    def __init__(self, what: str, error: type[NotInstalledError] = NotInstalledError) -> None:
        self.what = what
        self.error = error
        self.value: T | None = None
        _slots.add(self)

    def install(self, value: T) -> None:
        """Install ``value``; installing again replaces it, so wiring is idempotent."""
        self.value = value

    def get(self) -> T:
        if self.value is None:
            raise self.error(f"{self.what} is not installed: this process never called the application startup hook")
        return self.value

    @contextmanager
    def installed(self, value: T) -> Iterator[T]:
        """Install ``value`` for the block, then restore whatever was installed before."""
        previous = self.value
        self.value = value
        try:
            yield value
        finally:
            self.value = previous


# Every slot alive in this process, so a test-isolation check can find them without importing their owners.
_slots: weakref.WeakSet[Installed[Any]] = weakref.WeakSet()


def all_slots() -> tuple[Installed[Any], ...]:
    """Every installed slot that exists in this process."""
    return tuple(_slots)
