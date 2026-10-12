"""Real Celery schedules must yield until due and retain their early check."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from celery import Celery
from celery.beat import PersistentScheduler
from celery.schedules import crontab, schedule

from due.django.runtime.beat import RecoveryScheduler

pytestmark = pytest.mark.no_db


class Clock:
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)

    def __call__(self):
        return Clock.now


def scheduler_journey(scheduler_type, tmp_path, *, cron=False):
    Clock.now = datetime(2026, 10, 13, tzinfo=UTC)
    clock = Clock()
    deliveries = []
    with (
        patch.object(Celery, "now", lambda self: Clock.now),
        Celery("deadline-proof", broker="memory://", set_as_current=False) as app,
    ):
        app.conf.beat_schedule = {
            "recovery": {
                "task": "proof.recover",
                "schedule": crontab("*", app=app) if cron else schedule(1, app=app),
            }
        }
        scheduler = scheduler_type(app, schedule_filename=str(tmp_path / "beat"), max_interval=300)
        # Use the real broker publish path, recording only the publication signal.
        from celery.signals import before_task_publish

        def published(sender=None, **kwargs):
            if sender == "proof.recover":
                deliveries.append(clock.now)

        before_task_publish.connect(published, weak=False)
        try:
            period = 60 if cron else 1
            checks = 0
            early_checks = 0
            while len(deliveries) < 12 and checks < 2000:
                interval = scheduler.tick()
                checks += 1
                if interval > 0:
                    # An ordinary sleep can overshoot. Include fixed scheduling cost.
                    Clock.now += timedelta(seconds=interval, microseconds=100)
                    remaining = (period - clock.now.timestamp() % period) % period
                    if 0 < remaining < 0.011:
                        early_checks += 1
                else:
                    Clock.now += timedelta(microseconds=100)
            assert len(deliveries) == 12
            for index, sent in enumerate(deliveries, 1):
                elapsed = (sent - datetime(2026, 10, 13, tzinfo=UTC)).total_seconds()
                assert index * period <= elapsed <= index * period + 0.01, "delivery was early or cadence drifted"
            assert early_checks >= 10, "intentional early deadline check disappeared"
            assert checks <= 48, f"future deadlines must yield, not spin: {checks} checks"
        finally:
            before_task_publish.disconnect(published)
            scheduler.close()


@pytest.mark.parametrize("cron", [False, True])
def test_future_deadlines_sleep_without_losing_early_checks_or_cadence(tmp_path, cron):
    scheduler_journey(RecoveryScheduler, tmp_path, cron=cron)


def test_negative_control_original_scheduler_spins(tmp_path):
    with pytest.raises(AssertionError, match="future deadlines must yield"):
        scheduler_journey(PersistentScheduler, tmp_path)


def test_early_adjustment_preserves_heap_order_for_neighboring_deadlines(tmp_path):
    """A second entry 7 ms later must not overtake the first inside the drift window."""
    Clock.now = datetime(2026, 10, 13, tzinfo=UTC)
    with (
        patch.object(Celery, "now", lambda self: Clock.now),
        Celery("neighboring-deadlines", broker="memory://", set_as_current=False) as app,
    ):
        app.conf.beat_schedule = {
            "first": {"task": "first", "schedule": 0.005},
            "second": {"task": "second", "schedule": 0.012},
        }
        scheduler = RecoveryScheduler(app, schedule_filename=str(tmp_path / "neighbors"))
        try:
            delay = scheduler.tick()
            Clock.now += timedelta(seconds=delay)
            final_delay = scheduler.tick()
            assert 0 < delay + final_delay <= 0.005, "later entry delayed the earliest deadline"
        finally:
            scheduler.close()
