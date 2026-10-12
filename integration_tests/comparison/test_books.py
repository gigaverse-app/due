"""The same immutable-book outcomes under equivalent external failure histories."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import connection, transaction
from procrastinate.contrib.django.models import ProcrastinateJob
from pytest_obligation.process_histories import fault_environment, fault_happened

from due.django.testing import hold_nudges
from examples.books import search
from examples.books.models import Book, IndexBookWork
from examples.books.services import create_book as create_due_book
from examples.comparison.tasks import QUEUE, recover_stalled
from examples.comparison.tasks import create_book as create_procrastinate_book
from integration_tests.conformance.book_examples import recover_process

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    ("backend", "fault", "queued_recovery"),
    [
        ("due", None, False),
        ("procrastinate", None, False),
        ("due", "after admission", False),
        ("procrastinate", "after admission", False),
        ("due", "after index write", False),
        ("procrastinate", "after index write", False),
        ("due", "after activation", False),
        ("procrastinate", "after activation", False),
        ("procrastinate", "after index write", True),
    ],
)
def test_shared_book_outcome_after_process_death(
    backend: str, fault: str | None, queued_recovery: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    title = uuid4().hex
    marker = tmp_path / "fault"
    monkeypatch.setenv("DUE_BOOK_INDEX", str(tmp_path / "index.sqlite3"))
    base = os.environ.get("DUE_DATABASE_URL", "postgresql://due:due@127.0.0.1:55439/due")
    env = {
        **os.environ,
        "DUE_DATABASE_URL": base.rsplit("/", 1)[0] + "/" + str(connection.settings_dict["NAME"]),
        "DJANGO_SETTINGS_MODULE": "examples.comparison.settings",
        **fault_environment(fault, marker),
    }
    module = (
        "integration_tests.harness.book_process" if backend == "due" else "integration_tests.harness.comparison_process"
    )
    result = subprocess.run([sys.executable, "-m", module, title], env=env, capture_output=True, text=True, timeout=30)
    (tmp_path / f"{backend}-worker.log").write_text(result.stdout + result.stderr)
    if fault is None:
        assert result.returncode == 0, result.stderr
    else:
        assert fault_happened(marker), result.stderr
        assert result.returncode != 0
        book = Book.objects.get(title=title)
        assert not book.index_key
        if backend == "due":
            recover_process(title)
        else:
            # The producer/worker subprocess is known dead. Only shorten the
            # detection clock; Procrastinate's own selector and retry own recovery.
            if queued_recovery:
                # Reproduce the periodic scheduler winning the race before the
                # explicit recovery driver starts. The killed worker is gone.
                if not ProcrastinateJob.objects.filter(
                    queueing_lock="comparison-stalled-recovery", status="todo"
                ).exists():
                    recover_stalled.configure(task_kwargs={"timestamp": 0}).defer()
            env.update(fault_environment(None, marker))
            env["DUE_COMPARISON_STALE_SECONDS"] = "0"
            recovery = subprocess.run(
                [sys.executable, "-m", module, "recover"], env=env, capture_output=True, text=True, timeout=30
            )
            (tmp_path / "procrastinate-recovery.log").write_text(recovery.stdout + recovery.stderr)
            assert recovery.returncode == 0, recovery.stderr
    book = Book.objects.get(title=title)
    assert book.index_key
    assert search.search(title) == [book.index_key]
    assert search.get(book.index_key) == search.IndexEntry(
        key=book.index_key, title=title, author="Process Author", published=True
    )
    # Provider truth, independently of either library's internal job/work state.
    with search.connection() as provider:
        assert provider.execute("SELECT count(*) FROM books WHERE title = ?", (title,)).fetchone()[0] == 1


@pytest.mark.parametrize("backend", ["due", "procrastinate"])
def test_shared_admission_rollback_leaves_no_product_or_work(backend: str) -> None:
    produce = create_due_book if backend == "due" else create_procrastinate_book
    before = ProcrastinateJob.objects.filter(queue_name=QUEUE).count()
    with hold_nudges(), transaction.atomic():
        book = produce("Rollback", "Author")
        assert Book.objects.filter(pk=book.pk).exists()
        transaction.set_rollback(True)
    assert not Book.objects.filter(pk=book.pk).exists()
    assert not IndexBookWork.objects.filter(owner_id=book.pk).exists()
    assert ProcrastinateJob.objects.filter(queue_name=QUEUE).count() == before
