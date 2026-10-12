"""Scenario bindings observe real file bytes and enter the production commands."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.utils import timezone
from pytest_obligation.contract import NotApplicable
from pytest_obligation.process_histories import ProcessHistory
from pytest_obligation.profiles.catalog import ConvergenceFamily
from pytest_obligation.profiles.harmless_replay import ReplaySafeEffect

from due.django.conformance.delivery import WorkTableDelivery
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
from tests.examples.file_support import environment as environment
from tests.examples.file_support import probe
from tests.harness.processes import run_child

TEXT = "The next edition is ready."


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
        expected=TEXT,
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


def run_process(point: str | None) -> tuple[str, int]:
    key = str(uuid4())
    marker = Path(os.environ["DUE_OUTPUT_DIR"]) / f"fault-{key}"
    return key, run_child("tests.harness.scheduled_process", key, point, marker)


@dataclass(frozen=True)
class PublicationOutcome:
    """Independent product/provider projection used across fresh child identities."""

    status: str | None
    content: str | None
    deadline_obligations: int


def process_outcome(key: str) -> PublicationOutcome:
    publication = Publication.objects.filter(submission_key=key).first()
    if publication is None:
        return PublicationOutcome(None, None, 0)
    content = None
    if publication.published_key:
        path = Path(os.environ["DUE_OUTPUT_DIR"]) / publication.published_key
        content = path.read_text() if path.exists() else None
    return PublicationOutcome(
        publication.status, content, PublicationDeadline.objects.filter(owner=publication).count()
    )


def recover_process(key: str) -> None:
    with WorkTableDelivery(publish_at_deadline).session() as delivery_session:
        delivery_session.recover()


EXAMPLES = EffectExamples(
    fixtures=("scheduled_files",),
    process_handoffs=(
        ProcessHistory(
            name="scheduled publication process death",
            initial=PublicationOutcome(None, None, 0),
            expected=PublicationOutcome(Publication.Status.PUBLISHED, TEXT, 1),
            run=run_process,
            observe=process_outcome,
            recover=recover_process,
            death_points=("after admission", "before file write", "after file write", "after settlement"),
        ),
    ),
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
