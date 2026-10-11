"""Test-owned capabilities for every mode's collection-time obligations; no external I/O."""

from typing import ClassVar, Never

from django.db import models
from django.test.utils import isolate_apps

from due.django.capabilities.compensation import IndependentCompensation
from due.django.capabilities.operations import DurableExternalOperation, ExternalOperationWork
from due.django.capabilities.projection import DurableProjection
from due.django.capabilities.projection import Projection as ProductProjection
from due.django.capabilities.scheduled import (
    DurableScheduledAction,
    ScheduledAction,
    ScheduledWork,
)
from due.django.evidence.effect_evidence import AppliedEffectEvidenceStore
from due.django.public import (
    DurableWorkPolicy,
    OwnedDurableWorkCapability,
    WorkClaim,
    WorkExecutionContext,
)
from tests.harness.primitive import PrimitiveOwner, PrimitiveWork, primitive


def no_fixture(*args: object, **kwargs: object) -> Never:
    raise AssertionError("examples must not execute while requiring missing proofs")


with isolate_apps("tests"):

    class EnrollmentOperationWork(ExternalOperationWork):
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(PrimitiveOwner, on_delete=models.CASCADE)

        class Meta:
            app_label = "tests"

    class EnrollmentScheduledWork(ScheduledWork):
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(PrimitiveOwner, on_delete=models.CASCADE)

        class Meta:
            app_label = "tests"


class Operation(DurableExternalOperation[PrimitiveOwner, EnrollmentOperationWork, str]):
    def perform_effect(self, work: EnrollmentOperationWork) -> str:
        return no_fixture()

    @property
    def evidence_store(self) -> AppliedEffectEvidenceStore[str]:
        return no_fixture()

    def finalize_applied(self, work: EnrollmentOperationWork, result: str) -> None:
        no_fixture()


class Scheduled(DurableScheduledAction[PrimitiveOwner, EnrollmentScheduledWork]):
    def is_still_needed(self, work: EnrollmentScheduledWork) -> bool:
        return no_fixture()

    def perform_effect(self, work: EnrollmentScheduledWork) -> None:
        no_fixture()


class Projection(DurableProjection[PrimitiveOwner, PrimitiveWork]):
    def write_projection(self, work: PrimitiveWork) -> None:
        no_fixture()


class CallbackProjection(ProductProjection[PrimitiveOwner, PrimitiveWork]):
    def write_projection(self, work: PrimitiveWork) -> None:
        no_fixture()


class CallbackScheduled(ScheduledAction[PrimitiveOwner, EnrollmentScheduledWork]):
    def is_still_needed(self, work: EnrollmentScheduledWork) -> bool:
        return no_fixture()

    def perform_effect(self, work: EnrollmentScheduledWork) -> None:
        no_fixture()


class Compensation(IndependentCompensation[PrimitiveWork, PrimitiveWork]):
    def capture(self, producer: WorkClaim[PrimitiveWork]) -> PrimitiveWork:
        return no_fixture()

    def compensate(self, work: PrimitiveWork) -> bool:
        return no_fixture()


class CustomOwned(OwnedDurableWorkCapability[PrimitiveOwner, PrimitiveWork, None]):
    def apply(self, work: PrimitiveWork, context: WorkExecutionContext[PrimitiveWork]) -> None:
        no_fixture()


TEST_OWNED_CAPABILITIES = (
    CustomOwned.for_owner(owner=PrimitiveOwner, work=PrimitiveWork, policy=DurableWorkPolicy()),
    primitive,
    Operation.for_owner(owner=PrimitiveOwner, work=EnrollmentOperationWork, policy=DurableWorkPolicy(retention=None)),
    Scheduled.for_owner(owner=PrimitiveOwner, work=EnrollmentScheduledWork, policy=DurableWorkPolicy(retention=None)),
    Projection.for_owner(owner=PrimitiveOwner, work=PrimitiveWork, policy=DurableWorkPolicy(retention=None)),
    CallbackProjection.for_owner(
        owner=PrimitiveOwner,
        work=PrimitiveWork,
        input_fields=("universe", "payload"),
        policy=DurableWorkPolicy(retention=None),
    ),
    CallbackScheduled.for_owner(
        owner=PrimitiveOwner,
        work=EnrollmentScheduledWork,
        input_fields=("universe", "run_at", "cancelled"),
        policy=DurableWorkPolicy(retention=None),
    ),
    Compensation(model=PrimitiveWork, nudge=no_fixture, policy=DurableWorkPolicy(), producer_model=PrimitiveWork),
)
