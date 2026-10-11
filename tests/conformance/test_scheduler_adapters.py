"""Beat and interleaving controls drive actual Celery task bodies without a broker."""

from collections.abc import Iterator

import pytest
from celery import Celery

from due.django.conformance.background_beat import beat_entries_for, tick
from due.django.conformance.interleavings import BrokerControl, environment

pytestmark = pytest.mark.django_db


@pytest.fixture
def tasks() -> Iterator[tuple[Celery, list[str]]]:
    app = Celery("scheduler_controls", broker="memory://", set_as_current=False)
    seen: list[str] = []

    @app.task(name="scheduler.child")
    def child(value: str) -> None:
        seen.append(value)

    @app.task(name="scheduler.tick")
    def scheduled(value: str) -> None:
        child.apply_async(args=(value,), countdown=10)

    app.conf.beat_schedule = {
        "selected": {"task": scheduled.name, "args": ("delivered",), "schedule": 60},
        "unrelated": {"task": child.name, "args": ("wrong",), "schedule": 60},
    }
    try:
        yield app, seen
    finally:
        app.close()


def test_beat_selection_delayed_delivery_and_redelivery(tasks: tuple[Celery, list[str]]) -> None:
    app, seen = tasks
    with environment() as (background, _):
        assert tick(background, seconds=60, beat=beat_entries_for("selected"), app=app) == ["selected"]
        assert seen == []
        background.advance(10)
        background.drain()
        assert seen == ["delivered"]
        control = BrokerControl(background)
        control.redeliver(len(background.executed) - 1)
        assert seen == ["delivered", "delivered"]
        with pytest.raises(AssertionError, match="captured"):
            control.redeliver(-1)


def test_admission_captures_then_drops_or_releases_notifications(tasks: tuple[Celery, list[str]]) -> None:
    app, seen = tasks
    with environment() as (background, _):
        control = BrokerControl(background)
        control.drop(True)
        with control.admission():
            app.tasks["scheduler.child"].delay("lost")
        control.start()
        assert not background.held and not seen
        control.drop(False)
        with control.admission():
            app.tasks["scheduler.child"].delay("released")
            assert not seen
        control.start()
        assert seen == ["released"]


def test_environment_surfaces_unclaimed_failure_and_restores_transport(tasks: tuple[Celery, list[str]]) -> None:
    app, _ = tasks
    task = app.tasks["scheduler.child"]
    original = type(task).apply_async
    with pytest.raises(AssertionError, match="never accounted"):
        with environment() as (background, _):
            task.delay()  # Real task argument validation fails in the driver's worker.
            assert background.failures
    assert type(task).apply_async is original
