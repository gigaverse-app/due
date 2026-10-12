from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

from pytest_obligation.contract import NotApplicable
from pytest_obligation.profiles.catalog import ConvergenceFamily

from due.django.conformance.composition import ProjectionExample
from due.django.conformance.suite import AdmissionFailureExample, ProducerExample, TerminalExample
from due.django.testing import hold_nudges
from examples.books import search
from examples.catalog.effects import project_catalog
from examples.catalog.models import CatalogEntry, CatalogWork
from examples.catalog.services import retry_entry, save_entry
from tests.conformance.index_examples import BookContent, Stage, examples_for
from tests.examples.books_support import calls, index_environment

TITLE, AUTHOR = "First edition", "Catalog Author"


def make_work() -> CatalogWork:
    with hold_nudges():
        return save_entry(TITLE, AUTHOR)


def content(work: CatalogWork) -> BookContent | None:
    entry = search.get(work.index_key)
    return (entry.title, entry.author) if entry and entry.published else None


def rearm(work: CatalogWork) -> None:
    assert work.owner_id is not None
    assert retry_entry(work.owner_id)


def snapshot() -> ProjectionExample[CatalogWork, BookContent | None]:
    work = make_work()
    return ProjectionExample(
        work=work,
        change=lambda: save_entry("Second edition", AUTHOR, entry_id=work.owner_id),
        observe=lambda: content(work),
        latest=("Second edition", AUTHOR),
    )


@contextmanager
def producer() -> Iterator[ProducerExample[CatalogWork, bool]]:
    with index_environment():
        yield ProducerExample(
            produce=lambda: (save_entry(TITLE, AUTHOR),),
            observe=lambda: CatalogEntry.objects.filter(title=TITLE).exists(),
            expected=True,
            effect_calls=lambda: calls.get().writes.total,
        )


@contextmanager
def admission_failure() -> Iterator[AdmissionFailureExample[CatalogWork, bool]]:
    with producer() as scenario:
        yield AdmissionFailureExample.after_save(scenario, CatalogWork)


EXAMPLES = replace(
    examples_for(
        Stage(
            capability=project_catalog,
            make_work=make_work,
            observe=content,
            external_observe=content,
            effect_calls=lambda work: calls.get().writes.count(work.index_key),
            unavailable=lambda: calls.get().writes.reject(lambda: OSError("search unavailable")),
            execute=project_catalog.write_projection,
            terminal=TerminalExample(
                observe=lambda work: CatalogEntry.objects.get(pk=work.owner_id).indexing_failed, rearm=rearm
            ),
            expected=(TITLE, AUTHOR),
            external_expected=(TITLE, AUTHOR),
        )
    ),
    immutable_inputs=None,
    convergence_not_assessed=None,
    snapshot=snapshot,
    admissions={"save entry": producer},
    admission_failures={"save entry": admission_failure},
    convergence_families={
        family: NotApplicable(
            "This catalog overwrites one provider key; late-write repair and captured revisions own convergence, not a monotonic merge or dependency gate."
        )
        for family in ConvergenceFamily
        if family.name != "STALE_SNAPSHOTS"
    },
)
