from collections.abc import Iterator
from unittest.mock import patch

import pytest
from django.test import Client

from due.django.conformance.delivery import WorkTableDelivery
from due.django.public import DurableWorkState
from due.django.testing import WorkerDied, die_after, hold_nudges
from examples.books import search
from examples.books.effects import index_book
from examples.books.models import Book, IndexBookWork, PublishBookWork
from examples.books.services import create_book
from integration_tests.examples.books_support import index_environment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def local_index() -> Iterator[None]:
    with index_environment():
        yield


def test_browser_submission_becomes_searchable_after_recovery(client: Client) -> None:
    with hold_nudges():
        response = client.post("/books/new/", {"title": "Kindred", "author": "Octavia Butler"})
    assert response.status_code == 302
    assert b"Indexing pending" in client.get("/").content
    assert b"No books found." in client.get("/", {"q": "Kindred"}).content

    with WorkTableDelivery(index_book).session() as delivery:
        delivery.recover()

    assert b"Indexed" in client.get("/").content
    for query in ("kindred", "BUTLER"):
        assert b"Kindred" in client.get("/", {"q": query}).content
    assert b"Kindred" not in client.get("/", {"q": "unknown"}).content


@pytest.mark.parametrize("data", [{"title": "", "author": "Author"}, {"title": "x" * 101, "author": "Author"}])
def test_invalid_submission_does_not_admit_work(client: Client, data: dict[str, str]) -> None:
    response = client.post("/books/new/", data)
    assert response.status_code == 200
    assert not Book.objects.exists()
    assert not IndexBookWork.objects.exists()


def test_create_form_requires_csrf() -> None:
    client = Client(enforce_csrf_checks=True)
    response = client.get("/books/new/")
    assert response.status_code == 200
    assert b"csrfmiddlewaretoken" in response.content
    assert client.post("/books/new/", {"title": "Kindred", "author": "Octavia Butler"}).status_code == 403


def test_interrupted_successor_admission_rolls_back_index_settlement() -> None:
    with hold_nudges():
        book = create_book("Kindred", "Octavia Butler")
    work = IndexBookWork.objects.get(owner=book)
    with (
        hold_nudges(),
        patch.object(PublishBookWork, "save", die_after(PublishBookWork.save)),
        pytest.raises(WorkerDied),
    ):
        index_book.attempt_execution(work.pk)

    work.refresh_from_db()
    assert work.state != DurableWorkState.SUCCEEDED
    assert not PublishBookWork.objects.filter(owner=book).exists()
    assert Book.objects.get(pk=book.pk).index_key == ""

    with WorkTableDelivery(index_book).session() as delivery:
        delivery.recover()
    book.refresh_from_db()
    assert search.search("Kindred") == [book.index_key]
    assert PublishBookWork.objects.filter(owner=book).count() == 1
