"""Optional work-table conformance integration; install due[testing].

Enroll through due.django.conformance.suite; author declarations in
due.django.conformance.examples and scopes in due.django.conformance.binding. Importing the optional
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
