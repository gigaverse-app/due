"""Selected Celery Beat entries running through the canonical background-work driver.

Task loading is cached per Celery application; applications retain schedule
ownership. Import this module only in tests that run Beat.
"""

import functools
from collections.abc import Callable
from typing import Any

from celery import Celery, current_app

from due.django.conformance.background import BackgroundWork, Delivery

BeatSelector = Callable[[str, dict[str, Any]], bool]


@functools.cache
def _register_beat_task_modules(app: Celery) -> None:
    """
    Import the beat-scheduled task modules once per test process.

    Celery's Django fixup answers every ``import_default_modules()`` with a
    full Django system-check run (models, URL config, templates). Beat ticks
    once per simulated scheduler period, so a four-hour golden paid 244 check
    runs: 76% of its runtime. The modules stay imported for the life of the
    process, so registering them once is all a tick needs.
    """
    app.loader.import_default_modules()


def run_beat(work: BackgroundWork, select: BeatSelector, *, app: Celery | None = None) -> list[str]:
    """Run the selected beat entries once, as beat would, then deliver what they dispatched."""
    app = current_app if app is None else app
    _register_beat_task_modules(app)
    ran: list[str] = []
    for key, entry in list(app.conf.beat_schedule.items()):
        if not select(key, entry):
            continue
        task = app.tasks[entry["task"]]
        work.deliver(Delivery(task, tuple(entry.get("args", ())), dict(entry.get("kwargs", {})), None, None, None))
        ran.append(key)
    work.drain()
    return ran


def tick(work: BackgroundWork, *, seconds: float, beat: BeatSelector, app: Celery | None = None) -> list[str]:
    """One scheduler period: time passes, then the selected beat entries run."""
    work.advance(seconds)
    return run_beat(work, beat, app=app)


def beat_entries_for(*keywords: str) -> BeatSelector:
    """Select beat entries whose key, task path or args mention any keyword."""

    def select(key: str, entry: dict[str, Any]) -> bool:
        haystack = f"{key} {entry.get('task', '')} {entry.get('args', ())}"
        return any(keyword in haystack for keyword in keywords)

    return select
