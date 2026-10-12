"""Real HTTP and independent web/worker/Beat startup recover lost notifications."""

import re
import sys
from html import escape
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, build_opener

import pytest

from due.django.public import DurableWorkState
from due.django.testing import hold_nudges
from examples.books.effects import index_book
from examples.books.models import Book, IndexBookWork, PublishBookWork
from examples.books.services import create_book
from integration_tests.integration.processes import running_process
from integration_tests.integration.worker import running_worker

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("lost_stage", ["index_book", "publish_book"])
def test_live_catalog_and_beat_recover_lost_notification(
    lost_stage: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unused_tcp_port: int
) -> None:
    base = f"http://127.0.0.1:{unused_tcp_port}"
    browser = build_opener(HTTPCookieProcessor(CookieJar()))

    def get(path: str) -> str:
        with browser.open(base + path, timeout=2) as response:
            return response.read().decode()

    def web_ready() -> bool:
        try:
            return "csrfmiddlewaretoken" in get("/books/new/")
        except URLError:
            return False

    def searchable(title: str) -> bool:
        return f"<li>{escape(title)}" in get("/?" + urlencode({"q": title}))

    with (
        running_worker(tmp_path, monkeypatch) as worker,
        running_process(
            tmp_path,
            "web",
            [sys.executable, "-m", "django", "runserver", f"127.0.0.1:{unused_tcp_port}", "--noreload"],
            worker.env,
        ) as web,
    ):
        web.wait_until(web_ready)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', get("/books/new/"))
        assert token is not None
        title = "The Left Hand of Darkness"
        form = urlencode({"title": title, "author": "Ursula K. Le Guin", "csrfmiddlewaretoken": token[1]})
        with browser.open(base + "/books/new/", data=form.encode(), timeout=5) as response:
            assert response.status == 200
        # Beat is absent: this must exercise the web process's configured broker.
        worker.wait_until(lambda: searchable(title))
        assert Book.objects.filter(title=title).count() == 1

        # Commit through the application, but suppress all wake-ups until the
        # desired boundary. There is no direct recovery call in this journey.
        with hold_nudges() as lost:
            book = create_book("Kindred", "Octavia Butler")
            if lost_stage == "publish_book":
                index_book.complete(IndexBookWork.objects.get(owner=book))
        assert lost
        stranded_model = IndexBookWork if lost_stage == "index_book" else PublishBookWork
        assert stranded_model.objects.get(owner=book).state == DurableWorkState.READY
        assert not searchable(book.title)

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
            beat.wait_until(lambda: searchable(book.title))
            assert worker.process.handle.poll() is None, worker.process.log.read_text()
            book.refresh_from_db()
            assert book.index_key
            assert not book.indexing_failed
            for model in (IndexBookWork, PublishBookWork):
                work = model.objects.get(owner=book)
                assert work.state == DurableWorkState.SUCCEEDED
                assert work.applied_revision == work.desired_revision
