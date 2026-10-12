"""
The shared broker must contain dispatch even after an instance-level test patch.

Design: docs/architecture.md#verification.


Design: docs/architecture.md#verification.
"""

import pytest
from celery import Celery
from celery import current_app as celery_app

from due.django.conformance.background import BackgroundWork
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)


@pytest.mark.no_db
def test_background_driver_captures_restored_instance_method(monkeypatch: pytest.MonkeyPatch) -> None:
    def identity(value: str) -> str:
        return value

    task = celery_app.task(name="tests.durable.restored_transport", lazy=False)(identity)
    try:
        # MonkeyPatch restores the bound inherited method on the instance.
        # A subsequent patch of Task.apply_async alone no longer intercepts it.
        with monkeypatch.context() as previous_test:
            previous_test.setattr(task, "apply_async", lambda **kwargs: None)
        assert "apply_async" in task.__dict__

        def escaped(*args: object, **kwargs: object) -> None:
            raise AssertionError("dispatch escaped the fake broker")

        monkeypatch.setattr(Celery, "send_task", escaped)
        driver = BackgroundWork().install(monkeypatch)
        task.apply_async(args=("payload",), countdown=60)
        assert len(driver.held) == 1
        assert driver.held[0].task is task
        assert driver.held[0].args == ("payload",)
    finally:
        celery_app.tasks.pop(task.name)


@pytest.mark.usefixtures("isolated_primitive")
def test_recovery_drain_records_worker_failure_and_checks_every_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    from due.django.runtime.tasks import run_durable_work_task
    from tests.harness.primitive import PrimitiveEffect, record

    with monkeypatch.context() as broker_patch:
        # The test registration captures sends; restore the actual Celery broker seam.
        broker_patch.delattr(run_durable_work_task, "apply_async")
        driver = BackgroundWork().install(broker_patch)
        work = record()

        def broken(self: PrimitiveEffect, work) -> str:
            raise RuntimeError("broken worker")

        monkeypatch.setattr(PrimitiveEffect, "perform_effect", broken)
        with pytest.raises(RuntimeError, match="broken worker"):
            driver.run_recovery("primitive_self_test")
        deliveries = driver.work_deliveries("primitive_self_test")
        assert len(deliveries) == 1
        assert deliveries[0].args[1] == work.pk
        assert driver.failures == deliveries
        assert str(driver.failures[0].error) == "broken worker"
        with pytest.raises(AssertionError, match="worker queue drifted"):
            run_durable_work_task.apply_async(
                args=("primitive_self_test", work.pk, "token"),
                queue="wrong",
                time_limit=60,
                soft_time_limit=50,
            )
