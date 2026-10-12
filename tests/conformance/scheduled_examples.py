"""Scenario bindings observe real file bytes and enter the production commands."""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.utils import timezone
from pytest_obligation.contract import NotApplicable
from pytest_obligation.profiles.catalog import ConvergenceFamily
from pytest_obligation.profiles.harmless_replay import ReplaySafeEffect

from due.django.conformance.provider import ProviderProbe
from due.django.conformance.suite import (
    AdmissionFailureExample,
    CancellationExample,
    DeliveryExample,
    EffectExamples,
    FailureExample,
    ProducerExample,
    TerminalExample,
)
from due.django.testing import hold_nudges
from examples.documents import storage
from examples.scheduled.effects import publish_at_deadline
from examples.scheduled.models import Publication, PublicationDeadline
from examples.scheduled.services import cancel_publication, create_publication, retry_publication

TEXT = "The next edition is ready."
probe: ContextVar[ProviderProbe[[str, str], str, str]] = ContextVar("scheduled_files")


@contextmanager
def environment() -> Iterator[None]:
    writer: Callable[[str, str], str] = storage.write_revision
    writes = ProviderProbe(writer, key=lambda text, operation_key: operation_key)
    token = probe.set(writes)
    try:
        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, {"DUE_OUTPUT_DIR": directory}),
            writes.patch(storage, "write_revision"),
        ):
            yield
    finally:
        probe.reset(token)


def make_work() -> PublicationDeadline:
    with hold_nudges():
        publication = create_publication(TEXT, timezone.now() - timedelta(seconds=1))
    return PublicationDeadline.objects.get(owner=publication)


def external(work: PublicationDeadline) -> str | None:
    path = Path(os.environ["DUE_OUTPUT_DIR"]) / storage.revision_key(work.provider_idempotency_key())
    return path.read_text() if path.exists() else None


def product(work: PublicationDeadline) -> str | None:
    publication = Publication.objects.get(pk=work.owner_id)
    if publication.status != Publication.Status.PUBLISHED:
        return None
    return (Path(os.environ["DUE_OUTPUT_DIR"]) / publication.published_key).read_text()


def calls(work: PublicationDeadline) -> int:
    return probe.get().count(work.provider_idempotency_key())


def delivery() -> DeliveryExample[PublicationDeadline, str | None]:
    work = make_work()
    return DeliveryExample(work, lambda: product(work), TEXT, lambda: calls(work))


@contextmanager
def retry() -> Iterator[FailureExample[PublicationDeadline]]:
    with probe.get().reject(lambda: OSError("file store unavailable")):
        yield FailureExample(attempts=calls)


def terminal() -> TerminalExample[PublicationDeadline]:
    def rearm(work: PublicationDeadline) -> None:
        assert work.owner_id is not None
        assert retry_publication(work.owner_id)

    return TerminalExample(
        observe=lambda work: Publication.objects.get(pk=work.owner_id).status == Publication.Status.FAILED, rearm=rearm
    )


def replay() -> ReplaySafeEffect:
    # ARRANGE: create an already-due publication through the shared command.
    # REAL PRODUCTION: enter the real scheduled file operation twice.
    # EXTERNAL SEAM: ProviderProbe counts calls while delegating to the file store.
    # OBSERVE: read actual file bytes independently of work settlement.
    return ReplaySafeEffect(
        name="scheduled immutable file",
        prepare=make_work,
        execute=publish_at_deadline.perform_effect,
        observe=external,
        execution_count_for=calls,
    )


def snapshot() -> CancellationExample[PublicationDeadline, str | None]:
    work = make_work()
    assert work.owner_id is not None
    owner_id = work.owner_id
    return CancellationExample(
        work=work, change=lambda: cancel_publication(owner_id), observe=lambda: external(work), completed=TEXT
    )


@contextmanager
def producer() -> Iterator[ProducerExample[PublicationDeadline, bool]]:
    with environment():

        def produce() -> tuple[PublicationDeadline]:
            publication = create_publication(TEXT, timezone.now() - timedelta(seconds=1))
            return (PublicationDeadline.objects.get(owner=publication),)

        yield ProducerExample(
            produce=produce,
            observe=lambda: Publication.objects.filter(text=TEXT).exists(),
            expected=True,
            effect_calls=lambda: probe.get().total,
        )


@contextmanager
def admission_failure() -> Iterator[AdmissionFailureExample[PublicationDeadline, bool]]:
    with producer() as scenario:
        yield AdmissionFailureExample.after_save(scenario, PublicationDeadline)


EXAMPLES = EffectExamples(
    fixtures=("scheduled_files",),
    environment=environment,
    make_work=make_work,
    delivery=delivery,
    retry=retry,
    terminal=terminal,
    replay=replay,
    snapshot=snapshot,
    admissions={"schedule publication": producer},
    admission_failures={"schedule publication": admission_failure},
    convergence_families={
        family: NotApplicable(
            "A pending deadline is replaced or cancelled; immutable revision files and a fenced product pointer need no merge or dependency gate."
        )
        for family in ConvergenceFamily
        if family.name != "STALE_SNAPSHOTS"
    },
)
