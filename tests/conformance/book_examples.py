"""Bind both book stages to the existing application conformance generator."""

import hashlib
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from django.db import connection
from pytest_obligation.process_histories import ProcessHistory, fault_environment, fault_happened

from due.django.conformance.delivery import WorkTableDelivery
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
from tests.conformance.index_examples import BookContent, Stage, examples_for
from tests.examples.books_support import calls, index_environment

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


def activated(work: PublishBookWork) -> bool:
    entry = search.get(work.index_key)
    return entry is not None and entry.published


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
            effect_calls=lambda: sum(calls.get().writes.values()),
        )


@contextmanager
def create_admission_failure() -> Iterator[AdmissionFailureExample[IndexBookWork, bool]]:
    with create_producer() as producer:
        yield AdmissionFailureExample.after_save(producer, IndexBookWork)


def run_process(point: str | None) -> ProcessResult:
    title = uuid4().hex
    base = os.environ.get("DUE_DATABASE_URL", "postgresql://due:due@127.0.0.1:55439/due")
    database_url = base.rsplit("/", 1)[0] + "/" + str(connection.settings_dict["NAME"])
    marker = Path(os.environ["DUE_BOOK_INDEX"]).parent / f"fault-{title}"
    result = subprocess.run(
        [sys.executable, "-m", "tests.harness.book_process", title],
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "examples.settings",
            "DUE_DATABASE_URL": database_url,
            **fault_environment(point, marker),
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if point is not None:
        assert fault_happened(marker), result.stderr
    else:
        assert result.returncode == 0, result.stderr
    return title, result.returncode


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


INDEX_EXAMPLES = examples_for(
    Stage(
        capability=index_book,
        make_work=make_index_work,
        key=index_key,
        observe=indexed,
        external_observe=index_content,
        effect_calls=lambda work: calls.get().writes[index_key(work)],
        provider_method="put",
        execute=index_book.perform_effect,
        terminal=BOOK_TERMINAL,
        expected=(TITLE, AUTHOR),
    )
)
INDEX_EXAMPLES = replace(
    INDEX_EXAMPLES,
    admissions={"create book": create_producer},
    admission_failures={"create book": create_admission_failure},
    process_handoffs=(
        ProcessHistory(
            name="book indexing and publication process death",
            initial=BookOutcome(False, 0, False),
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
PUBLISH_EXAMPLES = examples_for(
    Stage(
        capability=publish_book,
        make_work=make_publish_work,
        key=lambda work: work.index_key,
        observe=published,
        external_observe=activated,
        effect_calls=lambda work: calls.get().publications[work.index_key],
        provider_method="publish",
        execute=publish_book.perform_effect,
        terminal=BOOK_TERMINAL,
        expected=(TITLE, AUTHOR),
    )
)
PUBLISH_EXAMPLES = replace(
    PUBLISH_EXAMPLES,
    admission_not_applicable="Publication admission writes one successor row; the upstream handoff is covered by index_book process histories and the interrupted-settlement journey.",
)
