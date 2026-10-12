from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

import pytest
from pytest_obligation.contract import NotApplicable
from pytest_obligation.profiles.catalog import ConvergenceFamily

from due.django.conformance.composition import ProjectionExample
from due.django.conformance.scenarios import SynchronousCallerExample
from due.django.conformance.seams import ProviderSeam
from due.django.conformance.suite import AdmissionFailureExample, DeliveryExample, ProducerExample, TerminalExample
from due.django.testing import hold_nudges
from examples.books import search
from examples.catalog.effects import project_catalog
from examples.catalog.models import CatalogEntry, CatalogWork
from examples.catalog.services import retry_entry, save_and_project_entry, save_entry
from integration_tests.conformance.index_examples import BookContent, examples_for
from integration_tests.examples.books_support import calls, index_environment

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


@contextmanager
def synchronous_caller() -> Iterator[SynchronousCallerExample[BookContent | None]]:
    with index_environment() as provider:

        def admitted() -> CatalogWork:
            return CatalogWork.objects.get(owner__title=TITLE)

        def observe() -> BookContent | None:
            work = CatalogWork.objects.filter(owner__title=TITLE).first()
            return content(work) if work is not None else None

        yield SynchronousCallerExample(
            call=lambda: save_and_project_entry(TITLE, AUTHOR),
            repeat=lambda: save_and_project_entry(TITLE, AUTHOR, entry_id=admitted().owner_id),
            observe=observe,
            expected=(TITLE, AUTHOR),
            effect_calls=lambda: provider.writes.total,
            fail_effect=lambda: provider.writes.reject(lambda: OSError("search unavailable")),
            lose_response=lambda: provider.writes.lose_response(lambda: OSError("response lost")),
            failure=OSError,
        )


@contextmanager
def reopening_delivery() -> Iterator[DeliveryExample[CatalogWork, tuple[str, BookContent | None]]]:
    """A real old write lost its response, so the newer projection must keep repairing."""
    with index_environment() as provider, hold_nudges():
        old = save_entry("Old edition", AUTHOR)
        with (
            provider.writes.lose_response(lambda: TimeoutError("old write response lost")),
            pytest.raises(TimeoutError),
        ):
            project_catalog.attempt_execution(old.pk)
        work = save_entry(TITLE, AUTHOR, entry_id=old.owner_id)
        yield DeliveryExample(
            work=work,
            observe=lambda: (CatalogEntry.objects.get(pk=work.owner_id).published_title, content(work)),
            expected=(TITLE, (TITLE, AUTHOR)),
            effect_calls=lambda: provider.writes.total + provider.publications.total,
        )


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
    provider_expected=lambda provider, work: (TITLE, AUTHOR),
    calls=lambda provider: provider.writes.total,
    calls_for=lambda provider, work: provider.writes.count(work.index_key),
    refuse=lambda provider: provider.writes.reject(lambda: OSError("search unavailable")),
)


@contextmanager
def delivery() -> Iterator[DeliveryExample[CatalogWork, BookContent | None]]:
    with SEAM.delivery() as scenario:
        provider = calls.get()
        # Delivery counts provider entries; profile I separately counts one write_projection invocation.
        yield replace(scenario, effect_calls=lambda: provider.writes.total + provider.publications.total)


EXAMPLES = replace(
    examples_for(
        project_catalog,
        SEAM,
        TerminalExample(observe=lambda work: CatalogEntry.objects.get(pk=work.owner_id).indexing_failed, rearm=rearm),
    ),
    delivery=delivery,
    immutable_inputs=None,
    convergence_not_assessed=None,
    snapshot=snapshot,
    synchronous_callers={"save and project entry": synchronous_caller},
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
