"""Bind both book stages to the existing application conformance generator."""

import hashlib
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from pytest_obligation.process_histories import ProcessHistory, fault_environment, fault_happened
from pytest_obligation.profiles.harmless_replay import ReplaySafeEffect

from due.django.conformance.delivery import WorkTableDelivery
from due.django.conformance.suite import (
    AdmissionFailureExample,
    DeliveryExample,
    EffectExamples,
    FailureExample,
    ProducerExample,
    TerminalExample,
)
from due.django.testing import hold_nudges
from examples.books import search
from examples.books.effects import BookEffect, index_book, publish_book
from examples.books.models import Book, BookWork, IndexBookWork, PublishBookWork
from examples.books.services import create_book, retry_indexing
from tests.examples.books_support import calls, index_environment

TITLE = "The Left Hand of Darkness"
AUTHOR = "Ursula K. Le Guin"
type BookContent = tuple[str, str]
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


@dataclass(frozen=True)
class Stage[W: BookWork]:
    capability: BookEffect[W]
    make_work: Callable[[], W]
    key: Callable[[W], str]
    observe: Callable[[W], BookContent | None]
    external_observe: Callable[[W], BookContent | bool | None]
    effect_calls: Callable[[W], int]
    provider_method: str


def examples_for[W: BookWork](stage: Stage[W]) -> EffectExamples[W]:
    def delivery() -> DeliveryExample[W, BookContent | None]:
        work = stage.make_work()
        return DeliveryExample(work, lambda: stage.observe(work), (TITLE, AUTHOR), lambda: stage.effect_calls(work))

    @contextmanager
    def retry() -> Iterator[FailureExample[W]]:
        attempts: Counter[str] = Counter()

        def fail(value: search.IndexEntry | str) -> str:
            attempts[value.key if isinstance(value, search.IndexEntry) else value] += 1
            raise OSError("search provider unavailable")

        with patch.object(search, stage.provider_method, fail):
            yield FailureExample(attempts=lambda work: attempts[stage.key(work)])

    def rearm(work: W) -> None:
        assert work.owner_id is not None
        assert retry_indexing(work.owner_id)

    def replay() -> ReplaySafeEffect:
        # ARRANGE: admit through the real book command and, for publication, its predecessor.
        # REAL PRODUCTION: execute the stage's actual provider operation twice.
        # EXTERNAL SEAM: index_environment records SQLite writes without replacing them.
        # OBSERVE: read the independently committed search entry or activation bit.
        return ReplaySafeEffect(
            name=stage.capability.model._meta.label,
            prepare=stage.make_work,
            execute=stage.capability.perform_effect,
            observe=stage.external_observe,
            execution_count_for=stage.effect_calls,
        )

    return EffectExamples(
        fixtures=("book_index",),
        make_work=stage.make_work,
        delivery=delivery,
        retry=retry,
        terminal=lambda: TerminalExample(
            observe=lambda work: Book.objects.get(pk=work.owner_id).indexing_failed, rearm=rearm
        ),
        replay=replay,
        immutable_inputs="The demo accepts immutable book submissions; editing or replacing a book is not exposed.",
        environment=index_environment,
    )


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


INDEX_EXAMPLES = examples_for(
    Stage(
        capability=index_book,
        make_work=make_index_work,
        key=index_key,
        observe=indexed,
        external_observe=index_content,
        effect_calls=lambda work: calls.get().writes[index_key(work)],
        provider_method="put",
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
    )
)
PUBLISH_EXAMPLES = replace(
    PUBLISH_EXAMPLES,
    admission_not_applicable="Publication admission writes one successor row; the upstream handoff is covered by index_book process histories and the interrupted-settlement journey.",
)
