"""Application testing entry point; install django-due[testing].

Import common contract bindings and fault controls here. Detailed declarations
remain available in their original modules. Exports resolve lazily so loading
the pytest reporting plugin before Django setup never imports ORM models.
"""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from due.django.testing import die_after as die_after
    from due.django.testing import expire_lease as expire_lease
    from due.django.testing import hold_nudges as hold_nudges

    from .binding import Binding as Binding
    from .binding import entered_binding as entered_binding
    from .bindings import DurableWorkBindings as DurableWorkBindings
    from .delivery import WorkTableDelivery as WorkTableDelivery
    from .examples import EffectExamples as EffectExamples
    from .host import work_table_host as work_table_host
    from .interleavings import WorkTableRecovery as WorkTableRecovery
    from .provider import ProviderProbe as ProviderProbe
    from .seams import ProviderSeam as ProviderSeam
    from .seams import seam_examples as seam_examples
    from .suite import effect_contract_suite as effect_contract_suite

_EXPORT_MODULES = {
    "Binding": ".binding",
    "DurableWorkBindings": ".bindings",
    "EffectExamples": ".examples",
    "ProviderProbe": ".provider",
    "ProviderSeam": ".seams",
    "WorkTableDelivery": ".delivery",
    "WorkTableRecovery": ".interleavings",
    "die_after": "due.django.testing",
    "effect_contract_suite": ".suite",
    "entered_binding": ".binding",
    "expire_lease": "due.django.testing",
    "hold_nudges": "due.django.testing",
    "seam_examples": ".seams",
    "work_table_host": ".host",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str):
    if name not in _EXPORT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORT_MODULES[name], __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
