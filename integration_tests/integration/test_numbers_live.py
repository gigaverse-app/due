"""The trivial tutorial's lost nudge recovers through independent worker and Beat processes."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from due.django.conformance import hold_nudges
from due.django.conformance.registrations import durable_work_installed
from due.django.public import DurableWorkState
from due.django.runtime.registry import registration_for
from examples.numbers.effects import request_number
from examples.numbers.models import Calculation, CalculationWork
from integration_tests.integration.processes import running_process
from integration_tests.integration.worker import running_worker


@pytest.mark.django_db(transaction=True)
def test_worker_and_beat_recover_the_tutorial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with running_worker(tmp_path, monkeypatch) as worker:
        immediate = Calculation.objects.create()
        with durable_work_installed(replace(registration_for(CalculationWork), queue=worker.queue)):
            request_number(immediate.pk, 21)
        worker.wait_until(lambda: Calculation.objects.get(pk=immediate.pk).result == 42)

        stranded = Calculation.objects.create()
        with hold_nudges() as lost:
            work = request_number(stranded.pk, 7)
        assert len(lost) == 1
        assert CalculationWork.objects.get(pk=work.pk).state == DurableWorkState.READY
        assert Calculation.objects.get(pk=stranded.pk).result is None

        with running_process(
            tmp_path,
            "beat",
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "examples.celery_app:app",
                "beat",
                "--loglevel=INFO",
                "--schedule",
                str(tmp_path / "beat-schedule"),
            ],
            worker.env,
        ) as beat:
            beat.wait_until(lambda: Calculation.objects.get(pk=stranded.pk).result == 14)
            assert worker.process.handle.poll() is None, worker.process.log.read_text()
        work.refresh_from_db()
        assert work.state == DurableWorkState.SUCCEEDED
        assert work.applied_revision == work.desired_revision
