import io
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from due.django.runtime.registry import installed


@pytest.mark.no_db
def test_check_configuration_and_explicitly_unchecked_remote_health() -> None:
    output = io.StringIO()
    call_command("due_check", stdout=output)
    assert "Configuration: OK" in output.getvalue()
    assert "Recovery freshness: NOT CHECKED" in output.getvalue()


@pytest.mark.no_db
def test_check_rejects_missing_task_imports(settings) -> None:
    settings.CELERY_IMPORTS = ()
    with pytest.raises(CommandError, match="task imports"):
        call_command("due_check")


@pytest.mark.no_db
def test_check_rejects_missing_recovery_schedule(settings) -> None:
    settings.CELERY_BEAT_SCHEDULE = {}
    with pytest.raises(CommandError, match="Beat entry"):
        call_command("due_check")


@pytest.mark.no_db
def test_check_rejects_wrong_recovery_route(settings) -> None:
    schedules = {
        key: {**entry, "options": {**entry["options"], "queue": "wrong"}}
        for key, entry in settings.CELERY_BEAT_SCHEDULE.items()
    }
    settings.CELERY_BEAT_SCHEDULE = schedules
    with pytest.raises(CommandError, match="queue"):
        call_command("due_check")


@pytest.mark.no_db
def test_check_rejects_stale_or_absent_worker_metrics(tmp_path: Path) -> None:
    metrics = tmp_path / "worker.prom"
    metrics.write_text('due_last_recovery_timestamp_seconds{effect="books.indexbookwork"} 1\n')
    with pytest.raises(CommandError, match="recovery"):
        call_command("due_check", metrics_file=metrics)


@pytest.mark.no_db
def test_check_accepts_fresh_metrics_for_every_registered_capability(tmp_path: Path) -> None:
    metrics = tmp_path / "worker.prom"
    metrics.write_text(
        "\n".join(
            f'{name}{{effect="{entry.model._meta.label_lower}"}} {value}'
            for entry in installed.get().values()
            for name, value in (
                ("due_last_recovery_timestamp_seconds", timezone.now().timestamp() - 1),
                ("due_recovery_stale_after_seconds", 600),
            )
        )
    )
    output = io.StringIO()
    call_command("due_check", metrics_file=metrics, stdout=output)
    assert "Recovery freshness: OK" in output.getvalue()


@pytest.mark.no_db
def test_check_rejects_a_concrete_work_model_without_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(installed.get(), "index_book")
    with pytest.raises(CommandError, match="Unregistered work model: books.indexbookwork"):
        call_command("due_check")
