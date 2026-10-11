from django.db import transaction

from examples.books.effects import index_book, publish_book
from examples.books.models import Book, IndexBookWork


@transaction.atomic
def create_book(title: str, author: str, *, notify_worker: bool = True) -> Book:
    # The outer transaction includes creation: a failed admission must not leave
    # a visible book with no obligation. No provider I/O occurs in this command.
    book = Book(title=title, author=author)
    book.full_clean()
    book.save()
    with index_book.atomic(book.pk, notify_worker=notify_worker) as pending:
        pending.request_work(IndexBookWork(title=book.title, author=book.author, universe="example"))
    return book


@transaction.atomic
def retry_indexing(book_id: int) -> bool:
    """An explicit operator decision grants a stopped stage another retry budget."""
    retried = False
    for capability in (index_book, publish_book):
        with capability.atomic(book_id) as pending:
            retried = pending.retry_failed() or retried
    if retried:
        Book.objects.filter(pk=book_id).update(indexing_failed=False)
    return retried
