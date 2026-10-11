from pathlib import Path

import pytest

from due.django.public import DurableWorkState
from due.django.runtime.registry import registration_named
from due.django.testing import hold_nudges
from examples.books import search
from examples.books.effects import index_book
from examples.books.models import Book, IndexBookWork, PublishBookWork
from examples.books.services import create_book
from tests.integration.worker import running_worker

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("lost_stage", ["index_book", "publish_book"])
def test_worker_recovers_book_to_searchable_catalog(
    lost_stage: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with running_worker(tmp_path, monkeypatch) as worker:
        with hold_nudges() as lost:
            book = create_book("A Wizard of Earthsea", "Ursula K. Le Guin")
            if lost_stage == "publish_book":
                index_book.complete(IndexBookWork.objects.get(owner=book))
        assert any(nudge.work_name == registration_named(lost_stage).capability.work_name for nudge in lost)
        assert Book.objects.get(pk=book.pk).index_key == ""
        assert not search.search("Earthsea")

        worker.recover(lost_stage)

        def published() -> bool:
            book.refresh_from_db()
            return bool(book.index_key)

        worker.wait_until(published)
        assert search.search("Earthsea") == [book.index_key]
        assert search.get(book.index_key) == search.IndexEntry(
            key=book.index_key, title=book.title, author=book.author, published=True
        )
        assert not book.indexing_failed
        for model in (IndexBookWork, PublishBookWork):
            work = model.objects.get(owner=book)
            assert work.state == DurableWorkState.SUCCEEDED
            assert work.applied_revision == work.desired_revision
