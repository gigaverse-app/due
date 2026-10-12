"""Bind both book stages to the existing application conformance generator."""

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from pytest_obligation.process_histories import ProcessHistory

from due.django.conformance.delivery import WorkTableDelivery
from due.django.conformance.seams import ProviderSeam
from due.django.conformance.suite import (
    AdmissionFailureExample,
    ProducerExample,
    TerminalExample,
)
from due.django.testing import hold_nudges
from examples.books import search
from examples.books.effects import index_book, publish_book
from examples.books.models import Book, BookWork, IndexBookWork, PublishBookWork
from examples.books.services import create_book, retry_indexing
from tests.conformance.index_examples import BookContent, examples_for
from tests.examples.books_support import calls, index_environment
from tests.harness.processes import run_child

TITLE = "The Left Hand of Darkness"
AUTHOR = "Ursula K. Le Guin"

type ProcessResult = tuple[str, int]


def make_index_work() -> IndexBookWork:
    with hold_nudges():
        book = create_book(TITLE, AUTHOR)
    return IndexBookWork.objects.get(owner=book)


def make_publish_work() -> PublishBookWork:
    work = make_index_work()
    with hold_nudges():
        index_book.complete(work)
    return PublishBookWork.objects.get(owner_id=work.owner_id)


def index_key(work: IndexBookWork) -> str:
    return hashlib.sha256(work.provider_idempotency_key().encode()).hexdigest()


def index_content(work: IndexBookWork) -> BookContent | None:
    entry = search.get(index_key(work))
    return (entry.title, entry.author) if entry else None


def indexed(work: IndexBookWork) -> BookContent | None:
    return index_content(work) if PublishBookWork.objects.filter(owner_id=work.owner_id).exists() else None


def published(work: PublishBookWork) -> BookContent | None:
    book = Book.objects.get(pk=work.owner_id)
    entry = search.get(book.index_key)
    return (entry.title, entry.author) if entry and entry.published else None


def activated(work: PublishBookWork) -> BookContent | None:
    entry = search.get(work.index_key)
    return (entry.title, entry.author) if entry and entry.published else None


@contextmanager
def create_producer() -> Iterator[ProducerExample[IndexBookWork, bool]]:
    with index_environment():
        title = uuid4().hex

        def produce() -> tuple[IndexBookWork, ...]:
            book = create_book(title, AUTHOR)
            return (IndexBookWork.objects.get(owner=book),)

        yield ProducerExample(
            produce=produce,
            observe=lambda: Book.objects.filter(title=title).exists(),
            expected=True,
            effect_calls=lambda: calls.get().writes.total,
        )


@contextmanager
def create_admission_failure() -> Iterator[AdmissionFailureExample[IndexBookWork, bool]]:
    with create_producer() as producer:
        yield AdmissionFailureExample.after_save(producer, IndexBookWork)


def run_process(point: str | None) -> ProcessResult:
    title = uuid4().hex
    marker = Path(os.environ["DUE_BOOK_INDEX"]).parent / f"fault-{title}"
    return title, run_child("tests.harness.book_process", title, point, marker)


@dataclass(frozen=True)
class BookOutcome:
    visible: bool
    publication_obligations: int
    searchable: bool


def process_outcome(title: str) -> BookOutcome:
    book = Book.objects.filter(title=title).first()
    if book is None:
        return BookOutcome(False, 0, False)
    entry = search.get(book.index_key)
    return BookOutcome(
        bool(book.index_key),
        PublishBookWork.objects.filter(owner=book).count(),
        entry is not None and entry.published and entry.title == title and entry.author == "Process Author",
    )


def recover_process(title: str) -> None:
    # Both production recovery owners run: the first may already have settled
    # while its successor's notification vanished. Neither stage is inferred
    # from a message or manually moved to a successful state.
    for driver in (WorkTableDelivery(index_book), WorkTableDelivery(publish_book)):
        with driver.session() as delivery:
            delivery.recover()


def rearm_book(work: BookWork) -> None:
    assert work.owner_id is not None
    assert retry_indexing(work.owner_id)


BOOK_TERMINAL = TerminalExample(
    observe=lambda work: Book.objects.get(pk=work.owner_id).indexing_failed,
    rearm=rearm_book,
)


# ARRANGE: create a book through its application command.
# REAL PRODUCTION: IndexBook.perform_effect and fenced successor admission.
# EXTERNAL SEAM: observe and fault the real SQLite search provider.
# OBSERVE: independently read the index and the durable publication successor.
INDEX_SEAM = ProviderSeam(
    environment=index_environment,
    make_work=make_index_work,
    observe=lambda provider, work: indexed(work),
    provider_state=lambda provider, work: index_content(work),
    delivered=lambda provider, work: (TITLE, AUTHOR),
    calls=lambda provider: provider.writes.total,
    calls_for=lambda provider, work: provider.writes.count(index_key(work)),
    refuse=lambda provider: provider.writes.reject(lambda: OSError("search unavailable")),
)
INDEX_EXAMPLES = examples_for(index_book, INDEX_SEAM, BOOK_TERMINAL)
INDEX_EXAMPLES = replace(
    INDEX_EXAMPLES,
    admissions={"create book": create_producer},
    admission_failures={"create book": create_admission_failure},
    process_handoffs=(
        ProcessHistory(
            name="book indexing and publication process death",
            initial=BookOutcome(False, 0, False),
            expected=BookOutcome(True, 1, True),
            run=run_process,
            observe=process_outcome,
            recover=recover_process,
            death_points=(
                "after admission",
                "before index write",
                "after index write",
                "after handoff",
                "before activation",
                "after activation",
            ),
        ),
    ),
)
# ARRANGE: finish the real indexing stage to admit its publication successor.
# REAL PRODUCTION: PublishBook.perform_effect and fenced reader visibility.
# EXTERNAL SEAM: observe and fault actual SQLite publication writes.
# OBSERVE: read search visibility independently from the product pointer.
PUBLISH_SEAM = ProviderSeam(
    environment=index_environment,
    make_work=make_publish_work,
    observe=lambda provider, work: published(work),
    provider_state=lambda provider, work: activated(work),
    delivered=lambda provider, work: (TITLE, AUTHOR),
    calls=lambda provider: provider.publications.total,
    calls_for=lambda provider, work: provider.publications.count(work.index_key),
    refuse=lambda provider: provider.publications.reject(lambda: OSError("search unavailable")),
)
PUBLISH_EXAMPLES = examples_for(publish_book, PUBLISH_SEAM, BOOK_TERMINAL)
PUBLISH_EXAMPLES = replace(
    PUBLISH_EXAMPLES,
    admission_not_applicable="Publication admission writes one successor row; the upstream handoff is covered by index_book process histories and the interrupted-settlement journey.",
)
