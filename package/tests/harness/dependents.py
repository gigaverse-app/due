"""
A test-owned dependent that reads its owner's product value, for cross-work wake proofs.

Upstream capabilities in the root tests settle a fact onto ``PrimitiveOwner``;
``ReadOwner`` is existing work that must re-read it. Design and lock order:
docs/architecture.md.
"""

from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any, ClassVar
from uuid import UUID

import pytest
from django.db import connection, models
from django.test.utils import isolate_apps

from due.django.capabilities.compensation import IndependentCompensation
from due.django.declarations.dependents import Dependent
from due.django.public import (
    DurableWorkPolicy,
    ReplaySafeEffect,
    UniverseDurableWork,
    durable_work_schema,
)
from due.django.runtime.coordination import WorkClaim
from due.django.runtime.registry import DurableWorkRegistration
from tests.harness.primitive import PrimitiveOwner, PrimitiveWork
from tests.harness.registrations import durable_work_installed

with isolate_apps("tests"):

    class DependentWork(UniverseDurableWork):
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(PrimitiveOwner, on_delete=models.SET_NULL, null=True, related_name="+")

        class Meta:
            app_label = "tests"
            indexes, constraints = durable_work_schema(name_prefix="dependent")


class ReadOwner(ReplaySafeEffect[PrimitiveOwner, DependentWork, str]):
    """Reads the owner's value; ``settled`` holds what each committed pass saw."""

    deliveries: ClassVar[list[tuple[int, UUID]]] = []
    settled: ClassVar[list[str]] = []
    #: Runs once, after the read and before settlement: the window a fence must close.
    after_read: ClassVar[Callable[[], object] | None] = None

    def perform_effect(self, work: DependentWork) -> str:
        value = PrimitiveOwner.objects.get(pk=work.owner_id).value
        hook = type(self).after_read
        if hook is not None:
            type(self).after_read = None
            hook()
        return value

    def finalize_success(self, work: DependentWork, result: str) -> None:
        self.settled.append(result)


def deliver(work_id: int, token: UUID) -> None:
    ReadOwner.deliveries.append((work_id, token))


dependent = ReadOwner.for_owner(
    owner=PrimitiveOwner,
    work=DependentWork,
    nudge=deliver,
    input_fields=("universe",),
    policy=DurableWorkPolicy(retry_delay=timedelta(0)),
)


class ClaimedCleanup(IndependentCompensation[PrimitiveWork, DependentWork]):
    """A cleanup whose rows its producer claims: the shape a wake must never target."""

    def capture(self, producer: WorkClaim[PrimitiveWork]) -> DependentWork:
        raise AssertionError("registration checks never run cleanup")

    def compensate(self, work: DependentWork) -> bool:
        raise AssertionError("registration checks never run cleanup")


claimed_cleanup = ClaimedCleanup(
    producer_model=PrimitiveWork, model=DependentWork, nudge=deliver, policy=DurableWorkPolicy()
)


def dependent_of(work: Any) -> int | None:
    """The upstream's ``via``: the same owner's dependent, if one was admitted."""
    return DependentWork.objects.filter(owner_id=work.owner_id).values_list("pk", flat=True).first()


WAKES_DEPENDENT: tuple[Dependent[Any], ...] = (Dependent(DependentWork, via=dependent_of),)


def record_dependent(owner: PrimitiveOwner) -> DependentWork:
    with dependent.atomic(owner.pk, notify_worker=False) as pending:
        return pending.request_work(DependentWork(universe="acme"))


@pytest.fixture(name="dependent_tables")
def dependent_tables_fixture(isolated_primitive: None) -> Iterator[None]:
    ReadOwner.deliveries.clear()
    ReadOwner.settled.clear()
    ReadOwner.after_read = None
    with connection.schema_editor() as editor:
        editor.create_model(DependentWork)
    try:
        # A dependent is registered work: its notification resolves the registered capability.
        with durable_work_installed(
            DurableWorkRegistration(name="dependent_self_test", capability=dependent, queue="celery")
        ):
            yield
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(DependentWork)
