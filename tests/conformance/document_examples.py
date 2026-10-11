"""Adopter examples: real file publication, product commands and external faults."""

import hashlib
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.db import connection
from pytest_obligation.contract import NotApplicable
from pytest_obligation.process_histories import ProcessHistory, fault_environment, fault_happened
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
from examples.documents.effects import publish_document, request_publication, retry_publication
from examples.documents.models import Document, PublicationWork


@dataclass
class Files:
    root: Path
    writes: Counter[Path]


_files: ContextVar[Files] = ContextVar("document_conformance_files")


def destination(work: PublicationWork) -> Path:
    return _files.get().root / hashlib.sha256(work.provider_idempotency_key().encode()).hexdigest()


@contextmanager
def environment() -> Iterator[None]:
    with TemporaryDirectory() as directory:
        state = Files(Path(directory), Counter())
        original = Path.write_text

        def write(path: Path, data: str, *args, **kwargs) -> int:
            state.writes[path] += 1
            return original(path, data, *args, **kwargs)

        token = _files.set(state)
        try:
            with patch.dict(os.environ, {"DUE_OUTPUT_DIR": directory}), patch.object(Path, "write_text", write):
                yield
        finally:
            _files.reset(token)


def make_work() -> PublicationWork:
    with hold_nudges():
        return request_publication(Document.objects.create().pk, "published content")


def product(work: PublicationWork) -> str | None:
    document = Document.objects.get(pk=work.owner_id)
    return (_files.get().root / document.published_key).read_text() if document.published_key else None


def delivery() -> DeliveryExample[PublicationWork, str | None]:
    work = make_work()
    return DeliveryExample(
        work, lambda: product(work), work.text, effect_calls=lambda: _files.get().writes[destination(work)]
    )


@contextmanager
def retry() -> Iterator[FailureExample[PublicationWork]]:
    writes: Counter[Path] = Counter()

    def fail(path: Path, *args, **kwargs) -> int:
        writes[path] += 1
        raise OSError("storage unavailable")

    with patch.object(Path, "write_text", fail):
        yield FailureExample(attempts=lambda work: writes[destination(work)])


def terminal() -> TerminalExample[PublicationWork]:
    def rearm(work: PublicationWork) -> None:
        assert work.owner_id is not None
        assert retry_publication(work.owner_id)

    return TerminalExample(observe=lambda work: Document.objects.get(pk=work.owner_id).publication_failed, rearm=rearm)


def replay() -> ReplaySafeEffect:
    # ARRANGE: a fresh document admitted by its standalone command.
    # REAL PRODUCTION: the actual file writer, deliberately entered twice.
    # EXTERNAL SEAM: the environment records successful Path.write_text calls.
    # OBSERVE: bytes on disk, independently of Due's settlement and selection.
    return ReplaySafeEffect(
        name="immutable document file",
        prepare=make_work,
        execute=publish_document.perform_effect,
        observe=lambda work: destination(work).read_text() if destination(work).exists() else None,
        execution_count_for=lambda work: _files.get().writes[destination(work)],
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
            effect_calls=lambda: sum(_files.get().writes.values()),
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
                effect_calls=lambda: sum(_files.get().writes.values()),
            ),
            PublicationWork,
        )


def run_child(point: str | None) -> tuple[int, int]:
    document = Document.objects.create()
    base = os.environ.get("DUE_DATABASE_URL", "postgresql://due:due@127.0.0.1:55439/due")
    database_url = base.rsplit("/", 1)[0] + "/" + str(connection.settings_dict["NAME"])
    marker = _files.get().root / f"fault-{document.pk}"
    result = subprocess.run(
        [sys.executable, "-m", "tests.harness.document_process", str(document.pk)],
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
    return document.pk, result.returncode


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
