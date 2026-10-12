"""Adopter examples: real file publication, product commands and external faults."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pytest_obligation.contract import NotApplicable
from pytest_obligation.process_histories import ProcessHistory
from pytest_obligation.profiles.catalog import ConvergenceFamily
from pytest_obligation.profiles.harmless_replay import ReplaySafeEffect

from due.django.conformance.delivery import WorkTableDelivery
from due.django.conformance.suite import (
    AdmissionFailureExample,
    DeliveryExample,
    EffectExamples,
    FailureExample,
    ProducerExample,
    SupersessionExample,
    TerminalExample,
)
from due.django.testing import hold_nudges
from examples.documents import storage
from examples.documents.effects import publish_document, request_publication, retry_publication
from examples.documents.models import Document, PublicationWork
from tests.examples.file_support import environment as environment
from tests.examples.file_support import probe
from tests.harness.processes import run_child as run_demo_child


def destination(work: PublicationWork) -> Path:
    return Path(os.environ["DUE_OUTPUT_DIR"]) / storage.revision_key(work.provider_idempotency_key())


def calls(work: PublicationWork) -> int:
    return probe.get().count(work.provider_idempotency_key())


def make_work() -> PublicationWork:
    with hold_nudges():
        return request_publication(Document.objects.create().pk, "published content")


def product(work: PublicationWork) -> str | None:
    document = Document.objects.get(pk=work.owner_id)
    return (Path(os.environ["DUE_OUTPUT_DIR"]) / document.published_key).read_text() if document.published_key else None


def delivery() -> DeliveryExample[PublicationWork, str | None]:
    work = make_work()
    return DeliveryExample(work, lambda: product(work), work.text, effect_calls=lambda: calls(work))


@contextmanager
def retry() -> Iterator[FailureExample[PublicationWork]]:
    with probe.get().reject(lambda: OSError("storage unavailable")):
        yield FailureExample(attempts=calls)


def terminal() -> TerminalExample[PublicationWork]:
    def rearm(work: PublicationWork) -> None:
        assert work.owner_id is not None
        assert retry_publication(work.owner_id)

    return TerminalExample(observe=lambda work: Document.objects.get(pk=work.owner_id).publication_failed, rearm=rearm)


def replay() -> ReplaySafeEffect:
    # ARRANGE: a fresh document admitted by its standalone command.
    # REAL PRODUCTION: the actual file writer, deliberately entered twice.
    # EXTERNAL SEAM: ProviderProbe records calls to the real immutable provider.
    # OBSERVE: bytes on disk, independently of Due's settlement and selection.
    return ReplaySafeEffect(
        name="immutable document file",
        prepare=make_work,
        execute=publish_document.perform_effect,
        observe=lambda work: destination(work).read_text() if destination(work).exists() else None,
        execution_count_for=calls,
    )


def snapshot() -> SupersessionExample[PublicationWork, str | None]:
    work = make_work()
    assert work.owner_id is not None
    owner_id = work.owner_id
    return SupersessionExample(
        work=work, change=lambda: request_publication(owner_id, "new content"), observe=lambda: product(work)
    )


@contextmanager
def producer() -> Iterator[ProducerExample[PublicationWork, str | None]]:
    with environment():
        document = Document.objects.create()
        yield ProducerExample(
            produce=lambda: (request_publication(document.pk, "published content"),),
            observe=lambda: (
                product(PublicationWork.objects.get(owner_id=document.pk))
                if PublicationWork.objects.filter(owner_id=document.pk).exists()
                else None
            ),
            expected="published content",
            effect_calls=lambda: probe.get().total,
        )


@contextmanager
def admission_failure() -> Iterator[AdmissionFailureExample[PublicationWork, str]]:
    with environment():
        document = Document.objects.create(text="before")
        yield AdmissionFailureExample.after_save(
            ProducerExample(
                produce=lambda: (request_publication(document.pk, "after"),),
                observe=lambda: Document.objects.get(pk=document.pk).text,
                expected="after",
                effect_calls=lambda: probe.get().total,
            ),
            PublicationWork,
        )


def run_child(point: str | None) -> tuple[int, int]:
    document = Document.objects.create()
    marker = Path(os.environ["DUE_OUTPUT_DIR"]) / f"fault-{document.pk}"
    return document.pk, run_demo_child("tests.harness.document_process", str(document.pk), point, marker)


def process_observation(document_id: int) -> tuple[str | None, bool, str | None]:
    work = PublicationWork.objects.filter(owner_id=document_id).first()
    return (product(work), destination(work).exists(), work.state) if work else (None, False, None)


def recover_process(document_id: int) -> None:
    # REAL PRODUCTION: same registered recovery and workers as ordinary delivery.
    with WorkTableDelivery(publish_document).session() as delivery_session:
        delivery_session.recover()


EXAMPLES = EffectExamples(
    fixtures=("document_files",),
    process_handoffs=(
        ProcessHistory(
            name="document process death",
            initial=(None, False, None),
            expected=("process content", True, "succeeded"),
            run=run_child,
            observe=process_observation,
            recover=recover_process,
            death_points=("after admission", "before file write", "after file write"),
        ),
    ),
    make_work=make_work,
    delivery=delivery,
    retry=retry,
    terminal=terminal,
    replay=replay,
    snapshot=snapshot,
    environment=environment,
    producers={"request publication": producer},
    admission_failures={"partial command": admission_failure},
    convergence_families={
        family: NotApplicable("File revisions have separate immutable keys; only a fenced pointer is settled.")
        for family in ConvergenceFamily
        if family.name != "STALE_SNAPSHOTS"
    },
)
