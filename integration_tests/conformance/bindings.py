from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from unittest.mock import patch

from django.db import connection
from pytest_obligation.profiles.indivisible_admission import AdmissionAtomicity, AdmissionPublication
from pytest_obligation.profiles.job_retry_limits import BoundedRetry
from tests.harness.primitive import PrimitiveWork, primitive, record

from due.django.conformance import DurableWorkBindings
from due.django.models import DurableWorkState
from due.django.runtime import transport
from due.django.runtime.coordination import outstanding_work
from due.django.testing import hold_nudges, make_due
from examples.documents.effects import PublishDocument, request_publication
from examples.documents.models import Document, PublicationWork
from tests.harness import primitive as fixture


@contextmanager
def retries() -> Iterator[BoundedRetry]:
    # ARRANGE: real obligations through the library producer.
    bindings = DurableWorkBindings(fixture.primitive, fixture.record)
    calls: Counter[int] = Counter()

    def fail(work: fixture.PrimitiveWork) -> str:
        # EXTERNAL SEAM: a provider outage, counted independently of retry state.
        calls[work.pk] += 1
        raise OSError("provider unavailable")

    run_attempt = fixture.primitive.attempt_execution

    def execute(work: fixture.PrimitiveWork) -> None:
        # REAL PRODUCTION: normal executor, including its failure settlement.
        with suppress(OSError):
            run_attempt(work.pk)

    maximum = fixture.primitive.policy.max_rearms
    assert maximum is not None
    with patch.object(fixture, "call", fail):
        yield BoundedRetry(
            name="Due retry budget",
            max_executions=maximum + 1,
            make_failing=bindings.make,
            due_work=fixture.primitive.due_work,
            run_once=execute,
            advance_to_due=bindings.age,
            is_terminal=lambda work: bindings.observe(work)[0] == DurableWorkState.EXHAUSTED,
            failure_attempt_count=lambda work: calls[work.pk],
            # OBSERVE: database state and independent provider call counts.
            observe=lambda work: (bindings.observe(work), calls[work.pk]),
            identity_of=lambda work: work.pk,
        )


class AdmissionInterrupted(Exception):
    pass


@contextmanager
def admission() -> Iterator[AdmissionAtomicity[str]]:
    # ARRANGE: an existing product row, before any obligation was admitted.
    document = Document.objects.create(text="before")

    @contextmanager
    def publications() -> Iterator[list[AdmissionPublication]]:
        events: list[AdmissionPublication] = []

        def capture(name: str, publish: Callable[[int], object], identity: int) -> None:
            # EXTERNAL SEAM: hold the broker wakeup and record its transaction boundary.
            events.append(AdmissionPublication(in_transaction=connection.in_atomic_block))

        with patch.object(transport, "publish_notification", capture):
            yield events

    @contextmanager
    def interrupt(checkpoint: Callable[[], None]) -> Iterator[None]:
        original = transport.notify_after_commit

        def fail(name: str, publish: Callable[[int], object], identity: int) -> None:
            original(name, publish, identity)
            checkpoint()
            raise AdmissionInterrupted("after product and obligation writes")

        with patch.object(transport, "notify_after_commit", fail):
            yield

    calls = 0
    original_effect = PublishDocument.perform_effect

    def counted(self: PublishDocument, work: PublicationWork) -> str:
        nonlocal calls
        calls += 1
        return original_effect(self, work)

    binding = AdmissionAtomicity(
        name="Due document admission",
        # REAL PRODUCTION: the runnable example's standalone product command.
        admit=lambda: [request_publication(document.pk, "after").pk],
        # OBSERVE: product state and complete obligation set, independent of returned ids.
        observe=lambda: Document.objects.get(pk=document.pk).text,
        expected="after",
        obligations=lambda: PublicationWork.objects.values_list("pk", flat=True),
        outstanding=lambda: outstanding_work(PublicationWork).values_list("pk", flat=True),
        effects=lambda: calls,
        publications=publications,
        during=interrupt,
        expected_error=AdmissionInterrupted,
    )

    with patch.object(PublishDocument, "perform_effect", counted):
        yield binding


def publish() -> int:
    with hold_nudges():
        return record(notify_worker=True).pk


def publish_retry() -> int:
    from due.django.runtime.coordination import claim_work, terminal_work

    work = record()
    claim = claim_work(PrimitiveWork, work.pk, lease_duration=primitive.policy.lease_duration)
    assert claim is not None
    terminal_work(claim, error_code="configuration_invalid")
    assert work.owner_id is not None
    with hold_nudges():
        with primitive.atomic(work.owner_id) as pending:
            assert pending.retry_failed()
    return work.pk


def age(work_id: int) -> None:
    make_due(PrimitiveWork.objects.get(pk=work_id))
