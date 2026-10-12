"""Beat and interleaving controls drive actual Celery task bodies without a broker."""

from collections.abc import Iterator

import pytest
from celery import Celery

from due.django.conformance.background_beat import beat_entries_for, tick
from due.django.conformance.interleavings import BrokerControl, environment
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

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


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("forever", [False, True])
@pytest.mark.parametrize("kind", ["work_table", "sweep"])
def test_handoff_recovers_downstream_etas_and_rejects_unobserved_work(
    tasks: tuple[Celery, list[str]], monkeypatch: pytest.MonkeyPatch, forever: bool, kind: str
) -> None:
    from due.django.conformance.bindings import DurableWorkBindings
    from due.django.conformance.delivery import SweepDelivery, WorkTableDelivery
    from tests.harness.primitive import primitive, record

    app, seen = tasks
    child = app.tasks["scheduler.child"]
    recorded: list[str] = []

    def record_only(value: str) -> None:
        recorded.append(value)

    monkeypatch.setattr(child, "delay", record_only)
    ticks = 0

    def recover_successor() -> None:
        nonlocal ticks
        ticks += 1
        if forever or ticks == 1:
            child.apply_async(args=("downstream",), countdown=7200)

    driver = (
        WorkTableDelivery(primitive, app=app)
        if kind == "work_table"
        else SweepDelivery(DurableWorkBindings(primitive, record).sweep, app=app)
    )
    with driver.session() as delivery:
        child.delay("lost")
        delivery.lose()
        if forever:
            with pytest.raises(AssertionError, match="still scheduled after 3 recovery rounds"):
                delivery.recover_with((recover_successor,))
        else:
            delivery.recover_with((recover_successor,))
            assert seen == ["downstream"]
        assert ticks == 3
        assert not recorded
    assert child.delay is record_only


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
def test_sweep_history_shares_binding_and_restores_nested_failure(tasks: tuple[Celery, list[str]]) -> None:
    from contextlib import contextmanager, nullcontext

    from pytest_obligation.crash_histories import HandoffHistory
    from pytest_obligation.profiles.automatic_recovery import DueWorkSweep

    from due.django.conformance.bindings import DurableWorkBindings
    from due.django.conformance.delivery import SweepDelivery
    from tests.harness.primitive import primitive, record

    bindings = DurableWorkBindings(primitive, record)

    opened = 0
    closed = 0

    @contextmanager
    def counted() -> Iterator[DueWorkSweep]:
        nonlocal opened, closed
        opened += 1
        try:
            with bindings.sweep() as sweep:
                yield sweep
        finally:
            closed += 1

    driver = SweepDelivery(counted, app=tasks[0])
    history = driver.scoped(
        HandoffHistory(
            name="binding lifetime",
            arrange=lambda: nullcontext("owner"),
            transition=lambda row: None,
            observe=lambda row: row,
            expected="owner",
        )
    )
    with history.arrange():
        with driver.session():
            assert opened == 1 and closed == 0
        with pytest.raises(RuntimeError, match="nested failure"):
            with history.arrange(), driver.session():
                assert opened == 2 and closed == 0
                raise RuntimeError("nested failure")
        with driver.session():
            assert opened == 2 and closed == 1
    assert closed == 2
    with driver.session():
        assert opened == 3 and closed == 2
    assert closed == 3


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("lose", [False, True])
def test_work_table_history_selectively_loses_optional_tasks(tasks: tuple[Celery, list[str]], lose: bool) -> None:
    from due.django.conformance.delivery import WorkTableDelivery
    from tests.harness.primitive import PrimitiveOwner, primitive, record

    app, seen = tasks
    child = app.tasks["scheduler.child"]
    route = WorkTableDelivery(primitive, lost_tasks=(child,) if lose else ())
    with route.session() as delivery:
        work = record()
        child.apply_async(args=("before recovery",), countdown=1)
        if lose:
            delivery.recover_with((lambda: child.apply_async(args=("during recovery",), countdown=1),))
        else:
            delivery.recover()
        assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "after"
        assert seen == ([] if lose else ["before recovery"])
        assert not delivery.driver.held
