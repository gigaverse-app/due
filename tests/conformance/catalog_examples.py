from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

from pytest_obligation.contract import NotApplicable
from pytest_obligation.profiles.catalog import ConvergenceFamily

from due.django.conformance.composition import ProjectionExample
from due.django.conformance.seams import ProviderSeam
from due.django.conformance.suite import AdmissionFailureExample, ProducerExample, TerminalExample
from due.django.testing import hold_nudges
from examples.books import search
from examples.catalog.effects import project_catalog
from examples.catalog.models import CatalogEntry, CatalogWork
from examples.catalog.services import retry_entry, save_entry
from tests.conformance.index_examples import BookContent, examples_for
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


# ARRANGE: admit editable metadata through save_entry.
# REAL PRODUCTION: the catalog projection writes and repairs the same provider key.
# EXTERNAL SEAM: record/refuse actual SQLite search writes.
# OBSERVE: read independently committed title/author and publication status.
SEAM = ProviderSeam(
    environment=index_environment,
    make_work=make_work,
    observe=lambda provider, work: content(work),
    provider_state=lambda provider, work: content(work),
    delivered=lambda provider, work: (TITLE, AUTHOR),
    calls=lambda provider: provider.writes.total,
    calls_for=lambda provider, work: provider.writes.count(work.index_key),
    refuse=lambda provider: provider.writes.reject(lambda: OSError("search unavailable")),
)
EXAMPLES = replace(
    examples_for(
        project_catalog,
        SEAM,
        TerminalExample(observe=lambda work: CatalogEntry.objects.get(pk=work.owner_id).indexing_failed, rearm=rearm),
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
        if family.name not in {"STALE_SNAPSHOTS", "MONOTONIC_RESULTS"}
    },
)
