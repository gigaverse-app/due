"""A single manifest must reach actual Django and Celery configuration safely."""

from copy import deepcopy

import pytest
from celery import Celery

from due.django.runtime.diagnostics import schedule_errors
from due.django.runtime.registry import RECOVERY_TASK, configure_durable_work, installed
from tests.harness.primitive import root_registration_fixture as root_registration_fixture

pytestmark = pytest.mark.no_db


def test_setup_binds_an_already_configured_celery_app_and_preserves_other_jobs(settings) -> None:
    entries = tuple(installed.get().values())
    other = {"nightly": {"task": "myapp.nightly", "schedule": 3600}}
    settings.CELERY_BEAT_SCHEDULE = deepcopy(other)
    settings.CELERY_IMPORTS = ("myapp.tasks",)
    with installed.installed(dict(installed.get())), Celery("setup", set_as_current=False) as app:
        app.conf.update(imports=("worker.tasks",), beat_schedule=deepcopy(other))
        for _ in range(2):
            configure_durable_work(entries, celery_app=app)
            assert installed.get() == {entry.name: entry for entry in entries}
            assert settings.CELERY_BEAT_SCHEDULE["nightly"] == other["nightly"]
            assert app.conf.beat_schedule["nightly"] == other["nightly"]
            assert app.conf.imports == ("worker.tasks", "myapp.tasks", "due.django.runtime.tasks")
            assert settings.CELERY_IMPORTS == ("myapp.tasks", "due.django.runtime.tasks")
            assert not [error for entry in entries for error in schedule_errors(entry, app)]


@pytest.mark.parametrize("collision_in", ["django", "celery"])
@pytest.mark.parametrize("conflict_kind", ["other-task", "other-route", "invalid-entry"])
def test_setup_refuses_schedule_collision_before_changing_any_configuration(
    settings, collision_in: str, conflict_kind: str
) -> None:
    previous = installed.get()
    entry = next(iter(previous.values()))
    collision = {
        "other-task": {"task": "myapp.unrelated", "schedule": 10},
        "other-route": {"task": RECOVERY_TASK, "args": ("another_route",), "schedule": 10},
        "invalid-entry": None,
    }[conflict_kind]
    conflict = {f"recover_{entry.name}": collision}
    settings.CELERY_BEAT_SCHEDULE = conflict if collision_in == "django" else {}
    settings.CELERY_IMPORTS = ("myapp.tasks",)
    with Celery("collision", set_as_current=False) as app:
        app.conf.update(beat_schedule=conflict if collision_in == "celery" else {}, imports=("worker.tasks",))
        before_django = deepcopy(settings.CELERY_BEAT_SCHEDULE)
        before_celery = deepcopy(app.conf.beat_schedule)
        with pytest.raises(ValueError, match="schedule.*owned"):
            configure_durable_work([entry], celery_app=app)
        assert installed.get() is previous
        assert settings.CELERY_BEAT_SCHEDULE == before_django
        assert app.conf.beat_schedule == before_celery
        assert settings.CELERY_IMPORTS == ("myapp.tasks",)
        assert app.conf.imports == ("worker.tasks",)


def test_settings_only_setup_is_seen_by_a_subsequently_loaded_celery_app(settings) -> None:
    entries = tuple(installed.get().values())
    settings.CELERY_IMPORTS = ()
    settings.CELERY_BEAT_SCHEDULE = {}
    with installed.installed(dict(installed.get())), Celery("lazy", set_as_current=False) as app:
        configure_durable_work(entries)
        app.config_from_object("django.conf:settings", namespace="CELERY")
        assert not [error for entry in entries for error in schedule_errors(entry, app)]


def test_reconfiguring_an_owned_schedule_updates_its_route_without_duplication(settings) -> None:
    from dataclasses import replace

    entry = next(iter(installed.get().values()))
    changed = replace(entry, cadence_seconds=19, queue="new-queue")
    settings.CELERY_IMPORTS = ()
    settings.CELERY_BEAT_SCHEDULE = {}
    with installed.installed(dict(installed.get())), Celery("changed", set_as_current=False) as app:
        configure_durable_work([entry], celery_app=app)
        configure_durable_work([changed], celery_app=app)
        assert not schedule_errors(changed, app)
        assert len(settings.CELERY_BEAT_SCHEDULE) == len(app.conf.beat_schedule) == 1
