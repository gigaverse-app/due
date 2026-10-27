"""Application testing entry point; install django-due[testing].

Import common contract bindings and fault controls here. Detailed declarations
remain available in their original modules. Exports resolve lazily so loading
the pytest reporting plugin before Django setup never imports ORM models.
Static exports live in __init__.pyi so runtime dependency scanners do not turn
ordinary helpers into real-worker startup consumers.
"""

from importlib import import_module

_EXPORT_MODULES = {
    "AdmissionFailureExample": "due.django.conformance.scenarios",
    "Binding": "due.django.conformance.binding",
    "DurableWorkBindings": "due.django.conformance.bindings",
    "EffectExamples": "due.django.conformance.examples",
    "ProviderProbe": "due.django.conformance.provider",
    "ProviderSeam": "due.django.conformance.seams",
    "ProducerExample": "due.django.conformance.scenarios",
    "SupersessionExample": "due.django.conformance.composition",
    "TerminalExample": "due.django.conformance.examples",
    "WorkerDied": "due.django.testing",
    "WorkTableDelivery": "due.django.conformance.delivery",
    "WorkTableRecovery": "due.django.conformance.interleavings",
    "applied_snapshot": "due.django.conformance.concurrency",
    "assert_complete_enrollment": "due.django.conformance.enrollment",
    "celery_worker_contract": "due.django.celery_testing",
    "die_after": "due.django.testing",
    "discover_enrollment": "due.django.conformance.enrollment",
    "effect_contract_suite": "due.django.conformance.suite",
    "entered_binding": "due.django.conformance.binding",
    "expire_lease": "due.django.testing",
    "hold_nudges": "due.django.testing",
    "seam_examples": "due.django.conformance.seams",
    "work_table_host": "due.django.conformance.host",
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
