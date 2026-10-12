"""
Test-owned rows exercise the real runtime without installing an adopter.

Design and proof boundaries: docs/architecture.md.
See the guide's verification module map for related production-bound suites.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import ClassVar
from uuid import UUID

import pytest
from django.db import connection, models
from django.db.models import QuerySet
from django.test.utils import isolate_apps

from due.django.public import (
    DurableWorkPolicy,
    DurableWorkState,
    ReplaySafeEffect,
    UniverseDurableWork,
    durable_work_schema,
)

with isolate_apps("tests"):

    class PrimitiveOwner(models.Model):
        value = models.CharField(max_length=40, default="before")
        claimed_at = models.DateTimeField(null=True)
        completed_at = models.DateTimeField(null=True)

        class Meta:
            app_label = "tests"

    class PrimitiveWork(UniverseDurableWork):
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(PrimitiveOwner, on_delete=models.SET_NULL, null=True)
        payload = models.CharField(max_length=40)

        class Meta:
            app_label = "tests"
            indexes, constraints = durable_work_schema(name_prefix="primitive")


class PrimitiveEffect(ReplaySafeEffect[PrimitiveOwner, PrimitiveWork, str]):
    calls: ClassVar[list[str]] = []
    deliveries: ClassVar[list[tuple[int, UUID]]] = []

    def perform_effect(self, work: PrimitiveWork) -> str:
        return call(work)

    def finalize_success(self, work: PrimitiveWork, result: str) -> None:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value=result)

    def finalize_retry(self, work: PrimitiveWork, error: Exception) -> None:
        # Exhaustion must be visible on the product row, not only on the work row.
        if work.state == DurableWorkState.EXHAUSTED:
            PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="exhausted")


def capture(work_id: int, token: UUID) -> None:
    PrimitiveEffect.deliveries.append((work_id, token))


primitive = PrimitiveEffect.for_owner(
    owner=PrimitiveOwner,
    work=PrimitiveWork,
    policy=DurableWorkPolicy(retry_delay=timedelta(0)),
)


@contextmanager
def primitive_tables() -> Iterator[None]:
    PrimitiveEffect.calls.clear()
    PrimitiveEffect.deliveries.clear()
    with connection.schema_editor() as editor:
        editor.create_model(PrimitiveOwner)
        editor.create_model(PrimitiveWork)
    try:
        yield
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(PrimitiveWork)
            editor.delete_model(PrimitiveOwner)


@contextmanager
def registered_primitive() -> Iterator[None]:
    from unittest.mock import patch

    from django.conf import settings
    from django.test import override_settings

    from due.django.runtime.registry import DurableWorkRegistration, recovery_schedules
    from tests.harness.registrations import durable_work_installed

    entry = DurableWorkRegistration(
        name="primitive_self_test",
        capability=primitive,
        cadence_seconds=60,
        queue="celery",
    )

    def deliver(*, args: tuple[str, int, str], **options: object) -> None:
        capture(args[1], UUID(args[2]))

    with (
        durable_work_installed(entry),
        override_settings(CELERY_BEAT_SCHEDULE={**settings.CELERY_BEAT_SCHEDULE, **recovery_schedules([entry])}),
        patch("due.django.runtime.tasks.run_durable_work_task.apply_async", side_effect=deliver),
    ):
        yield


def record(
    payload: str = "after", *, notify_worker: bool = False, owner: PrimitiveOwner | None = None
) -> PrimitiveWork:
    """The producer: one product owner plus its owed effect in one transaction."""
    if owner is None:
        owner = PrimitiveOwner.objects.create()
    with primitive.atomic(owner.pk, notify_worker=notify_worker) as pending:
        return pending.request_work(PrimitiveWork(payload=payload, universe="acme"))


def call(work: PrimitiveWork) -> str:
    PrimitiveEffect.calls.append(work.payload)
    return work.payload


@pytest.fixture(name="isolated_primitive")
def isolated_primitive_fixture(transactional_db: None) -> Iterator[None]:
    with primitive_tables(), registered_primitive():
        yield


@pytest.fixture(name="root_registration", autouse=True)
def root_registration_fixture() -> Iterator[None]:
    """Route the primitive self-test capability for every test in the importing module."""
    with registered_primitive():
        yield


def owed_owners() -> QuerySet[PrimitiveOwner]:
    # The application's "still owes work" predicate; completed_at settles it.
    return PrimitiveOwner.objects.filter(completed_at__isnull=True).order_by("pk")
