"""
Registry enrollment must reach the intended Beat arguments and worker queue.

Design and proof boundaries: docs/architecture.md#verification.
See the guide's verification module map for related production-bound suites.
"""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from celery import current_app as celery_app
from django.conf import settings
from django_celery_beat.schedulers import ModelEntry

from due.django.public import ReplayContract
from due.django.runtime.registry import (
    RECOVERY_TASK,
    index_durable_work_registrations,
    installed,
    recovery_schedules,
    remove_stale_recovery_schedules,
)
from due.django.runtime.tasks import capability_for, run_durable_work_task
from tests.harness.primitive import (
    root_registration_fixture as root_registration_fixture,
)


def test_registration_derives_its_model_from_the_capability_it_routes():
    from due.django.runtime.registry import DurableWorkRegistration
    from tests.harness.primitive import PrimitiveWork, primitive

    registration = DurableWorkRegistration(name="explicit_stable_route", capability=primitive)
    assert registration.model is PrimitiveWork
    with patch.dict(installed.get(), {registration.name: registration}):
        assert capability_for(registration.name) is primitive


@pytest.mark.no_db
@pytest.mark.parametrize("default_queue", ["celery", "ci-pr5257"])
def test_registration_default_queue_follows_environment_without_overriding_explicit_routes(settings, default_queue):
    from due.django.runtime.registry import DurableWorkRegistration
    from tests.harness.primitive import primitive

    settings.CELERY_TASK_DEFAULT_QUEUE = default_queue
    registration = DurableWorkRegistration(name="environment_default", capability=primitive)
    assert registration.queue == default_queue
    assert recovery_schedules([registration])["recover_environment_default"]["options"]["queue"] == default_queue
    explicit = DurableWorkRegistration(name="explicit_route", capability=primitive, queue="social_sharing")
    assert explicit.queue == "social_sharing"


def test_every_registered_capability_has_exact_schedule_arguments_queue_and_worker_budget():
    task_path = "due.django.runtime.tasks.recover_durable_work_task"
    assert "due.django.runtime.tasks" in celery_app.conf.imports
    for name, registration in installed.get().items():
        capability = capability_for(name)
        assert settings.CELERY_BEAT_SCHEDULE
        schedules = [
            entry
            for entry in settings.CELERY_BEAT_SCHEDULE.values()
            if entry["task"] == task_path and tuple(entry.get("args", ())) == (name,)
        ]
        assert len(schedules) == 1
        assert schedules[0]["schedule"] == registration.cadence_seconds
        assert schedules[0]["options"]["queue"] == registration.queue
        hard_limit = run_durable_work_task.time_limit
        assert hard_limit is not None
        assert capability.policy.lease_duration > timedelta(seconds=hard_limit)


def test_duplicate_name_or_work_table_cannot_get_two_recovery_owners():
    registration = installed.get()["primitive_self_test"]
    with pytest.raises(ValueError, match="duplicate.*name"):
        index_durable_work_registrations([registration, registration])
    with pytest.raises(ValueError, match="duplicate.*model"):
        index_durable_work_registrations([registration, replace(registration, name="other")])


def test_manifest_alone_controls_the_generated_cadence_and_queue():
    registration = replace(installed.get()["primitive_self_test"], cadence_seconds=17, queue="dedicated-cleanup")
    assert recovery_schedules([registration]) == {
        "recover_primitive_self_test": {
            "task": "due.django.runtime.tasks.recover_durable_work_task",
            "schedule": 17,
            "args": ("primitive_self_test",),
            "options": {"queue": "dedicated-cleanup", "expire_seconds": 17},
        }
    }


def test_a_recovery_slower_than_half_the_stale_threshold_is_refused():
    # recover() declares the default stale threshold; a slower cadence would page as stale between runs.
    slow = replace(installed.get()["primitive_self_test"], cadence_seconds=301)
    with pytest.raises(ValueError, match="cadence_seconds=301 is over half the stale threshold"):
        index_durable_work_registrations([slow])
    index_durable_work_registrations([replace(slow, cadence_seconds=300)])


@pytest.mark.django_db
def test_database_scheduler_updates_existing_recovery_entries_with_expiry():
    for registration in installed.get().values():
        name, configured = next(iter(recovery_schedules([registration]).items()))
        previous = ModelEntry.from_entry(
            name, app=celery_app, **{**configured, "options": {"queue": registration.queue}}
        )
        assert previous.model.expire_seconds is None
        entry = ModelEntry.from_entry(name, app=celery_app, **configured)
        entry.model.refresh_from_db()
        assert entry.model.pk == previous.model.pk
        assert entry.model.queue == registration.queue
        assert entry.model.expire_seconds == registration.cadence_seconds
        assert entry.options is not None
        assert entry.options["expires"] == registration.cadence_seconds


def test_worker_refuses_a_lease_shorter_than_its_hard_limit():
    capability = capability_for("primitive_self_test")
    invalid = replace(capability, policy=replace(capability.policy, lease_duration=timedelta(seconds=30)))
    with patch.dict(
        installed.get(), {"primitive_self_test": replace(installed.get()["primitive_self_test"], capability=invalid)}
    ):
        with pytest.raises(ValueError, match="lease must outlive"):
            capability_for("primitive_self_test")


@pytest.mark.parametrize("wrong_contract_on", ["model", "mode"])
def test_execution_mode_cannot_silently_replay_evidence_required_work(monkeypatch, wrong_contract_on):
    from tests.harness.primitive import PrimitiveEffect, PrimitiveWork

    capability = capability_for("primitive_self_test")
    assert replace(capability).execution_contract is ReplayContract.REPLAY_SAFE
    if wrong_contract_on == "model":
        monkeypatch.setattr(PrimitiveWork, "requires_effect_evidence", True)
    else:
        monkeypatch.setattr(PrimitiveEffect, "execution_contract", ReplayContract.EVIDENCE_REQUIRED)
    with pytest.raises(ValueError, match="only replay-safe"):
        replace(capability)


@pytest.mark.django_db
@pytest.mark.parametrize("task_name", [RECOVERY_TASK, "legacy.tasks.recover_durable_work_task", "older.tasks.recover"])
def test_stale_recovery_schedules_are_removed_and_nothing_else_is(task_name: str) -> None:
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    from due.django.runtime.registry import DurableWorkRegistration
    from tests.harness.primitive import primitive

    every_minute = IntervalSchedule.objects.create(every=60, period=IntervalSchedule.SECONDS)

    def persisted(name: str, task: str, args: str) -> None:
        PeriodicTask.objects.create(name=name, task=task, args=args, interval=every_minute)

    # What DatabaseScheduler left behind: an entry for a registration a branch deploy
    # had, the current registration's entry, and an unrelated task in another namespace.
    persisted("recover_recording_watch", task_name, '["recording_watch"]')
    persisted("recover_primitive_self_test", task_name, '["primitive_self_test"]')
    persisted("reconcile_event_cancellations", "other_app.tasks.reconcile_event_cancellations", "[]")

    persisted("foreign_recovery", "other_app.tasks.recover_durable_work_task", "[]")
    registrations = [DurableWorkRegistration(name="primitive_self_test", capability=primitive)]
    if task_name != RECOVERY_TASK:
        # Historical names are owned only after explicit application declaration.
        assert remove_stale_recovery_schedules(registrations) == []
    retired = () if task_name == RECOVERY_TASK else (task_name,)
    assert remove_stale_recovery_schedules(registrations, retired_task_names=retired) == ["recover_recording_watch"]
    assert set(PeriodicTask.objects.values_list("name", flat=True)) == {
        "recover_primitive_self_test",
        "reconcile_event_cancellations",
        "foreign_recovery",
    }
    assert remove_stale_recovery_schedules(registrations, retired_task_names=retired) == []  # Idempotent.
