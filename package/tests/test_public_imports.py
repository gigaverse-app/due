"""Application entry points must work in fresh processes and keep extras optional."""

import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.no_db


def run_python(source: str) -> None:
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "first_module",
    [
        "due.django.public",
        "due.django.capabilities.observed_operations",
        "due.django.capabilities.compensation",
        "due.django.runtime.registry",
    ],
)
def test_authoring_imports_work_without_optional_apps_or_testing(first_module):
    run_python(f"""
        import importlib
        import sys
        from django.conf import settings
        settings.configure(INSTALLED_APPS=["due.django"], SECRET_KEY="import-check")
        import django
        django.setup()
        importlib.import_module({first_module!r})
        from due.django.public import (
            ReplaySafeEffect, Projection, ScheduledAction, ScheduledWork,
            DurableExternalOperation, ExternalOperationWork,
            ObservedExternalOperation, ObservedExternalOperationWork, observation_index,
            IndependentCompensation, JsonAppliedEffectEvidenceStore, StoredAppliedEffectEvidence,
            DurableWorkPolicy, DurableWorkRegistration, configure_durable_work,
            django_tasks_registration, EffectOutcomeUnknownError,
        )
        from due.django.capabilities.projection import Projection as OriginalProjection
        from due.django.runtime.registry import configure_durable_work as original_configure
        assert Projection is OriginalProjection
        assert configure_durable_work is original_configure
        # Importing the facade cannot opt a native-only app into another model app or test extras.
        assert "due.django.tasks.models" not in sys.modules
        assert "pytest" not in sys.modules
        assert "pytest_obligation" not in sys.modules
    """)


@pytest.mark.parametrize("first_module", ["due.django.public", "due.django.tasks.runtime"])
@pytest.mark.parametrize("short_setup", [False, True])
def test_task_registration_from_facade_installs_real_routes(first_module, short_setup):
    run_python(f"""
        import importlib
        from django.conf import settings
        settings.configure(
            INSTALLED_APPS=["due.django", "due.django.tasks"], SECRET_KEY="import-check",
            TASKS={{"default": {{
                "BACKEND": "due.django.tasks.backend.DueBackend",
                "QUEUES": ["default", "facade"], "OPTIONS": {{"replay_safe_tasks": ["tests.tasks.decorated.greet"]}},
            }}}},
        )
        import django
        django.setup()
        importlib.import_module({first_module!r})
        from due.django.public import configure_durable_work, django_tasks_registration
        from due.django.tasks.runtime import django_tasks_registration as original_registration
        from due.django.tasks.models import TaskWork
        from due.django.runtime.registry import registration_for
        assert django_tasks_registration is original_registration
        if {short_setup!r}:
            configure_durable_work(django_tasks=True)
        else:
            entry = django_tasks_registration(recovery_queue="facade", cadence_seconds=5)
            configure_durable_work([entry])
            assert registration_for(TaskWork) is entry
        schedule = settings.CELERY_BEAT_SCHEDULE["recover_django_tasks"]
        assert schedule["args"] == ("django_tasks",)
        assert schedule["options"]["queue"] == ("default" if {short_setup!r} else "facade")
        assert schedule["schedule"] == (30 if {short_setup!r} else 5)
        before = dict(settings.CELERY_BEAT_SCHEDULE)
        installed_entry = registration_for(TaskWork)
        try:
            configure_durable_work([django_tasks_registration()], django_tasks=True)
        except ValueError:
            pass
        else:
            raise AssertionError("Duplicate task registration must be rejected")
        assert registration_for(TaskWork) is installed_entry
        assert settings.CELERY_BEAT_SCHEDULE == before
    """)


def test_conformance_entry_point_is_safe_before_django_setup():
    run_python("""
        import sys
        import due.django.conformance as conformance
        import due.django.conformance.reporting
        assert "due.django.models" not in sys.modules
        from django.conf import settings
        settings.configure(INSTALLED_APPS=["due.django"], SECRET_KEY="import-check")
        import django
        django.setup()
        from due.django.conformance import (
            DurableWorkBindings, EffectExamples, effect_contract_suite, work_table_host,
            ProviderSeam, ProviderProbe, seam_examples, WorkTableDelivery, WorkTableRecovery,
            hold_nudges, expire_lease, WorkerDied, die_after, applied_snapshot,
            ProducerExample, AdmissionFailureExample, SupersessionExample, TerminalExample,
            discover_enrollment, assert_complete_enrollment, celery_worker_contract,
        )
        from due.django.conformance.suite import effect_contract_suite as original_suite
        from due.django.testing import hold_nudges as original_hold
        assert effect_contract_suite is original_suite
        assert hold_nudges is original_hold
        from due.django.testing import celery_worker_contract as original_celery_contract
        assert celery_worker_contract is original_celery_contract
        stopped = die_after(lambda: 42)
        try:
            stopped()
        except WorkerDied:
            pass
        else:
            raise AssertionError("The public exception must catch the public fault control")
        assert stopped() == 42
        assert len(conformance.__all__) == len(set(conformance.__all__))
        for name in conformance.__all__:
            assert getattr(conformance, name) is not None
        try:
            conformance.missing_name
        except AttributeError:
            pass
        else:
            raise AssertionError("Unknown exports must raise AttributeError")
    """)
