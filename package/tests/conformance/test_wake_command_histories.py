"""
Self-test the generated wake-command histories against the real work-table runtime.

A wake command commits new facts for existing work and calls the capability's
``wake()``. The reference capability reads those facts when it executes, so a
woken READY, claimed or settled row must settle from them after recovery covers
the lost wake notification. Each negative control changes one seam the way an
adopter gets it wrong; the same generated assertion must reject it.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, ClassVar

import pytest
from django.db import transaction
from pytest_obligation.contract import ObligationContractDesignError

from due.django.conformance.examples import EffectExamples
from due.django.conformance.registrations import durable_work_installed
from due.django.conformance.scenarios import WAKE_HISTORIES, WakeExample, assert_wake_rereads_facts
from due.django.conformance.suite import effect_contract
from due.django.public import DurableWorkPolicy, ReplaySafeEffect
from due.django.runtime.registry import DurableWorkRegistration
from due.django.testing import hold_nudges
from tests.harness.operation import Publication, PublicationWork
from tests.harness.primitive import PrimitiveOwner, PrimitiveWork, record
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]

#: The product facts each owner's work reads when it executes.
FACTS: dict[int | None, str] = {}


class FactReader(ReplaySafeEffect[PrimitiveOwner, PrimitiveWork, str]):
    """Publishes the owner's current facts, read at execution time."""

    calls: ClassVar[list[int | None]] = []

    def perform_effect(self, work: PrimitiveWork) -> str:
        self.calls.append(work.owner_id)
        return FACTS[work.owner_id]

    def finalize_success(self, work: PrimitiveWork, result: str) -> None:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value=result)


class SnapshotReader(FactReader):
    """Defect: publishes the inputs captured at admission, so a wake changes nothing."""

    def perform_effect(self, work: PrimitiveWork) -> str:
        self.calls.append(work.owner_id)
        return work.payload


@contextmanager
def registered(kind: type[FactReader]) -> Iterator[FactReader]:
    capability = kind.for_owner(
        owner=PrimitiveOwner, work=PrimitiveWork, policy=DurableWorkPolicy(retry_delay=timedelta(0))
    )
    entry = DurableWorkRegistration(name="fact_reader", capability=capability, cadence_seconds=60, queue="celery")
    FACTS.clear()
    FactReader.calls.clear()
    with durable_work_installed(entry):
        yield capability


def wake(capability: FactReader, work: PrimitiveWork) -> None:
    # REAL PRODUCTION: commit the new facts and wake the existing work in one command.
    FACTS[work.owner_id] = "new facts"
    with transaction.atomic():
        capability.wake(work.pk)


def rerequest(capability: FactReader, work: PrimitiveWork) -> None:
    # Defect: requests the new value as a new revision instead of waking the existing work.
    FACTS[work.owner_id] = "new facts"
    assert work.owner_id is not None
    with capability.atomic(work.owner_id) as pending:
        pending.request_work(PrimitiveWork(payload="new facts", universe="acme"))


def nudge_only(capability: FactReader, work: PrimitiveWork) -> None:
    # Defect: publishes a worker notification but never wakes (or fences) the existing work.
    FACTS[work.owner_id] = "new facts"
    capability.enqueue_execution(work.pk)


def wake_example(
    capability: FactReader, command: Callable[[FactReader, PrimitiveWork], None]
) -> WakeExample[PrimitiveWork, str]:
    # ARRANGE: the real producer admits the work while the old facts hold.
    with hold_nudges():
        work = record(payload="old facts")
    FACTS[work.owner_id] = "old facts"
    return WakeExample(
        work=work,
        wake=lambda: command(capability, work),
        # OBSERVE: the product row and the provider entries for this owner.
        observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
        expected="new facts",
        effect_calls=lambda: FactReader.calls.count(work.owner_id),
    )


@pytest.mark.parametrize("history", WAKE_HISTORIES)
def test_a_woken_row_rereads_the_new_facts(history: str) -> None:
    with registered(FactReader) as capability, hold_nudges():
        assert_wake_rereads_facts(capability, wake_example(capability, wake), history=history)


@pytest.mark.parametrize(
    ("kind", "command", "history", "defect"),
    [
        (SnapshotReader, wake, "ready", "settles from the new facts"),
        (SnapshotReader, wake, "succeeded", "settles from the new facts"),
        (FactReader, rerequest, "ready", "woke this work 0 time"),
        (FactReader, nudge_only, "claimed", "woke this work 0 time"),
    ],
    ids=["snapshot-ignores-ready-wake", "snapshot-ignores-reopen", "re-request-instead-of-wake", "nudge-without-fence"],
)
def test_broken_wakes_are_rejected(
    kind: type[FactReader], command: Callable[[FactReader, PrimitiveWork], None], history: str, defect: str
) -> None:
    with registered(kind) as capability, hold_nudges(), pytest.raises(AssertionError, match=defect):
        assert_wake_rereads_facts(capability, wake_example(capability, command), history=history)


def declared_only(*args: object, **kwargs: object) -> Any:
    # ARRANGE: never entered; only the contract declaration is under test.
    raise AssertionError("a declaration-only binding must not execute")


@pytest.mark.no_db
def test_wake_commands_require_wakeable_work() -> None:
    publication = Publication.for_owner(
        owner=PrimitiveOwner, work=PublicationWork, policy=DurableWorkPolicy(retention=None)
    )
    examples: EffectExamples[PublicationWork] = EffectExamples(
        make_work=declared_only,
        delivery=declared_only,
        retry=declared_only,
        terminal=declared_only,
        applied_receipt=declared_only,
        receipt_repair=declared_only,
        rejections={"declared": declared_only},
        uncertainties={"declared": declared_only},
        immutable_inputs="An immutable provider occurrence.",
        wake_commands={"declared": declared_only},
    )
    entry = DurableWorkRegistration(name="publication", capability=publication, cadence_seconds=60, queue="celery")
    with (
        durable_work_installed(entry),
        pytest.raises(ObligationContractDesignError, match="wake_commands require work a command can wake"),
    ):
        effect_contract(publication, examples=examples)
