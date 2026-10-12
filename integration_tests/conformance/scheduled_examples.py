"""Scenario bindings observe real file bytes and enter the production commands."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from django.utils import timezone
from pytest_obligation.contract import NotApplicable
from pytest_obligation.process_histories import ProcessHistory
from pytest_obligation.profiles.catalog import ConvergenceFamily
from tests.harness.processes import run_child

from due.django.conformance.delivery import WorkTableDelivery
from due.django.conformance.seams import ProviderSeam, seam_examples
from due.django.conformance.suite import (
    AdmissionFailureExample,
    CancellationExample,
    ProducerExample,
    TerminalExample,
)
from due.django.testing import hold_nudges
from examples.documents import storage
from examples.scheduled.effects import publish_at_deadline
from examples.scheduled.models import Publication, PublicationDeadline
from examples.scheduled.services import cancel_publication, create_publication, retry_publication
from integration_tests.examples.file_support import file_environment as environment
from integration_tests.examples.file_support import files

TEXT = "The next edition is ready."


def make_work() -> PublicationDeadline:
    with hold_nudges():
        publication = create_publication(TEXT, timezone.now() - timedelta(seconds=1))
    return PublicationDeadline.objects.get(owner=publication)


def external(work: PublicationDeadline) -> str | None:
    path = files.get().root / storage.revision_key(work.provider_idempotency_key())
    return path.read_text() if path.exists() else None


def product(work: PublicationDeadline) -> str | None:
    publication = Publication.objects.get(pk=work.owner_id)
    if publication.status != Publication.Status.PUBLISHED:
        return None
    return (files.get().root / publication.published_key).read_text()


def calls(work: PublicationDeadline) -> int:
    return files.get().writes.count(work.provider_idempotency_key())


def terminal() -> TerminalExample[PublicationDeadline]:
    def rearm(work: PublicationDeadline) -> None:
        assert work.owner_id is not None
        assert retry_publication(work.owner_id)

    return TerminalExample(
        observe=lambda work: Publication.objects.get(pk=work.owner_id).status == Publication.Status.FAILED, rearm=rearm
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
            effect_calls=lambda: files.get().writes.total,
        )


@contextmanager
def admission_failure() -> Iterator[AdmissionFailureExample[PublicationDeadline, bool]]:
    with producer() as scenario:
        yield AdmissionFailureExample.after_save(scenario, PublicationDeadline)


def run_process(point: str | None) -> tuple[str, int]:
    key = str(uuid4())
    marker = files.get().root / f"fault-{key}"
    return key, run_child("integration_tests.harness.scheduled_process", key, point, marker)


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
        path = files.get().root / publication.published_key
        content = path.read_text() if path.exists() else None
    return PublicationOutcome(
        publication.status, content, PublicationDeadline.objects.filter(owner=publication).count()
    )


def recover_process(key: str) -> None:
    with WorkTableDelivery(publish_at_deadline).session() as delivery_session:
        delivery_session.recover()


# ARRANGE: schedule publication through the application command.
# REAL PRODUCTION: the deadline worker writes immutable bytes and settles status.
# EXTERNAL SEAM: real filesystem writes, observed/faulted through ProviderProbe.
# OBSERVE: persisted publication status and independently readable file bytes.
SEAM = ProviderSeam(
    environment=environment,
    make_work=make_work,
    observe=lambda provider, work: product(work),
    provider_state=lambda provider, work: external(work),
    delivered=lambda provider, work: TEXT,
    provider_expected=lambda provider, work: TEXT,
    calls=lambda provider: provider.writes.total,
    calls_for=lambda provider, work: provider.writes.count(work.provider_idempotency_key()),
    refuse=lambda provider: provider.writes.reject(lambda: OSError("file store unavailable")),
)
EXAMPLES = seam_examples(
    publish_at_deadline,
    SEAM,
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
    terminal=terminal,
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
