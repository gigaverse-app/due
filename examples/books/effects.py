"""Index a submission, then durably hand off publication to a second worker."""

import hashlib

from due.django.public import DurableWorkPolicy, DurableWorkState, ReplaySafeEffect
from examples.books import search
from examples.books.models import Book, BookWork, IndexBookWork, PublishBookWork


class BookEffect[W: BookWork](ReplaySafeEffect[Book, W, str]):
    def finalize_retry(self, work: W, error: Exception) -> None:
        if work.state == DurableWorkState.EXHAUSTED:
            Book.objects.filter(pk=work.owner_id).update(indexing_failed=True)


class IndexBook(BookEffect[IndexBookWork]):
    def perform_effect(self, work: IndexBookWork) -> str:
        key = hashlib.sha256(work.provider_idempotency_key().encode()).hexdigest()
        return search.put(search.IndexEntry(key=key, title=work.title, author=work.author))

    def finalize_success(self, work: IndexBookWork, result: str) -> None:
        assert work.owner_id is not None
        # Due calls this under fenced settlement. The successor must commit in
        # that SAME transaction: losing its notification must not lose publication.
        with publish_book.atomic(work.owner_id) as pending:
            pending.request_work(PublishBookWork(index_key=result, universe=work.universe))


class PublishBook(BookEffect[PublishBookWork]):
    def perform_effect(self, work: PublishBookWork) -> str:
        return search.publish(work.index_key)

    def finalize_success(self, work: PublishBookWork, result: str) -> None:
        # Only fenced settlement may make an external entry visible to readers.
        Book.objects.filter(pk=work.owner_id).update(index_key=result, indexing_failed=False)


index_book = IndexBook.for_owner(
    owner=Book, work=IndexBookWork, input_fields=("universe", "title", "author"), policy=DurableWorkPolicy()
)
publish_book = PublishBook.for_owner(
    owner=Book, work=PublishBookWork, input_fields=("universe", "index_key"), policy=DurableWorkPolicy()
)
