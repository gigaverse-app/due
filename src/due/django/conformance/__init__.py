"""Application testing entry point; install django-due[testing].

Import common contract bindings and fault controls here. Detailed declarations
remain available in their original modules. Exports resolve lazily so loading
the pytest reporting plugin before Django setup never imports ORM models.
"""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from due.django.celery_testing import celery_worker_contract as celery_worker_contract
    from due.django.testing import WorkerDied as WorkerDied
    from due.django.testing import die_after as die_after
    from due.django.testing import expire_lease as expire_lease
    from due.django.testing import hold_nudges as hold_nudges

    from .binding import Binding as Binding
    from .binding import entered_binding as entered_binding
    from .bindings import DurableWorkBindings as DurableWorkBindings
    from .composition import SupersessionExample as SupersessionExample
    from .concurrency import applied_snapshot as applied_snapshot
    from .delivery import WorkTableDelivery as WorkTableDelivery
    from .enrollment import assert_complete_enrollment as assert_complete_enrollment
    from .enrollment import discover_enrollment as discover_enrollment
    from .examples import EffectExamples as EffectExamples
    from .examples import TerminalExample as TerminalExample
    from .host import work_table_host as work_table_host
    from .interleavings import WorkTableRecovery as WorkTableRecovery
    from .provider import ProviderProbe as ProviderProbe
    from .scenarios import AdmissionFailureExample as AdmissionFailureExample
    from .scenarios import ProducerExample as ProducerExample
    from .seams import ProviderSeam as ProviderSeam
    from .seams import seam_examples as seam_examples
    from .suite import effect_contract_suite as effect_contract_suite

_EXPORT_MODULES = {
    "AdmissionFailureExample": ".scenarios",
    "Binding": ".binding",
    "DurableWorkBindings": ".bindings",
    "EffectExamples": ".examples",
    "ProviderProbe": ".provider",
    "ProviderSeam": ".seams",
    "ProducerExample": ".scenarios",
    "SupersessionExample": ".composition",
    "TerminalExample": ".examples",
    "WorkerDied": "due.django.testing",
    "WorkTableDelivery": ".delivery",
    "WorkTableRecovery": ".interleavings",
    "applied_snapshot": ".concurrency",
    "assert_complete_enrollment": ".enrollment",
    "celery_worker_contract": "due.django.celery_testing",
    "die_after": "due.django.testing",
    "discover_enrollment": ".enrollment",
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
