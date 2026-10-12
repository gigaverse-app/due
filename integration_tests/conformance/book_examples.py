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
from examples.books import search, services
from examples.books.effects import index_book, publish_book
from examples.books.models import Book, BookWork, IndexBookWork, PublishBookWork
from integration_tests.conformance.index_examples import BookContent, examples_for
from integration_tests.examples.books_support import calls, index_environment
from integration_tests.harness.processes import run_child

TITLE = "The Left Hand of Darkness"
AUTHOR = "Ursula K. Le Guin"

type ProcessResult = tuple[str, int]


def make_index_work() -> IndexBookWork:
    with hold_nudges():
        book = services.create_book(TITLE, AUTHOR)
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


def revisit_indexing(works: tuple[IndexBookWork, ...]) -> None:
    for work in works:
        assert work.owner_id is not None
        services.retry_indexing(work.owner_id)


def perturb_book(work: BookWork) -> None:
    # ARRANGE: move source facts, never the obligation under proof. The submitted
    # work must retain its captured content/key even if product metadata changes.
    Book.objects.filter(pk=work.owner_id).update(title="Changed title", author="Changed author", index_key="changed")


@contextmanager
def publication_producer() -> Iterator[ProducerExample[PublishBookWork, bool]]:
    with index_environment():
        work = make_index_work()
        result = index_book.perform_effect(work)

        def produce() -> tuple[PublishBookWork, ...]:
            # REAL PRODUCTION: the same settlement callback that admits the successor.
            index_book.finalize_success(work, result)
            return (PublishBookWork.objects.get(owner_id=work.owner_id),)

        yield ProducerExample(
            produce=produce,
            observe=lambda: PublishBookWork.objects.filter(owner_id=work.owner_id).exists(),
            expected=True,
            effect_calls=lambda: calls.get().publications.total,
        )


@contextmanager
def create_producer() -> Iterator[ProducerExample[IndexBookWork, bool]]:
    with index_environment():
        title = uuid4().hex

        def produce() -> tuple[IndexBookWork, ...]:
            book = services.create_book(title, AUTHOR)
            return (IndexBookWork.objects.get(owner=book),)

        yield ProducerExample(
            produce=produce,
            observe=lambda: Book.objects.filter(title=title).exists(),
            expected=True,
            effect_calls=lambda: calls.get().writes.total,
            revisit=revisit_indexing,
        )


@contextmanager
def create_admission_failure() -> Iterator[AdmissionFailureExample[IndexBookWork, bool]]:
    with create_producer() as producer:
        yield AdmissionFailureExample.after_save(producer, IndexBookWork)


def run_process(point: str | None) -> ProcessResult:
    title = uuid4().hex
    marker = Path(os.environ["DUE_BOOK_INDEX"]).parent / f"fault-{title}"
    return title, run_child("integration_tests.harness.book_process", title, point, marker)


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
    assert services.retry_indexing(work.owner_id)


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
    provider_expected=lambda provider, work: (TITLE, AUTHOR),
    calls=lambda provider: provider.writes.total,
    calls_for=lambda provider, work: provider.writes.count(index_key(work)),
    refuse=lambda provider: provider.writes.reject(lambda: OSError("search unavailable")),
)
INDEX_EXAMPLES = examples_for(index_book, INDEX_SEAM, BOOK_TERMINAL)
INDEX_EXAMPLES = replace(
    INDEX_EXAMPLES,
    immutable_inputs="Indexing captures the submission once; operator retry preserves that input and its identity.",
    perturb_inputs=perturb_book,
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
    provider_expected=lambda provider, work: (TITLE, AUTHOR),
    calls=lambda provider: provider.publications.total,
    calls_for=lambda provider, work: provider.publications.count(work.index_key),
    refuse=lambda provider: provider.publications.reject(lambda: OSError("search unavailable")),
)
PUBLISH_EXAMPLES = examples_for(publish_book, PUBLISH_SEAM, BOOK_TERMINAL)
PUBLISH_EXAMPLES = replace(
    PUBLISH_EXAMPLES,
    immutable_inputs="Publication captures the upstream result key; later metadata and repeated settlement cannot replace it.",
    perturb_inputs=perturb_book,
    producers={"index settlement": publication_producer},
    admission_not_applicable="Publication admission writes one successor row; the upstream handoff is covered by index_book process histories and the interrupted-settlement journey.",
)
