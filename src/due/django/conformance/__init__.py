"""Optional work-table conformance integration; install due[testing].

Full authoring API: due.django.conformance.suite. Importing the optional
reporting plugin before Django setup must not load ORM models.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bindings import DurableWorkBindings as DurableWorkBindings

__all__ = ["DurableWorkBindings"]


def __getattr__(name: str):
    if name == "DurableWorkBindings":
        from .bindings import DurableWorkBindings

        return DurableWorkBindings
    raise AttributeError(name)
