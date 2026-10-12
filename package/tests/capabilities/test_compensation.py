"""
Definite pre-write rejection discharges cleanup; uncertain output remains owed.

Design and proof boundaries: docs/architecture.md.
See the guide's verification module map for related production-bound suites.
"""

from typing import ClassVar

import pytest

from due.django.capabilities.compensation import IndependentCompensation
from due.django.public import (
    DurableWorkPolicy,
    DurableWorkState,
    OwnedDurableWorkCapability,
    WorkClaim,
    WorkExecutionContext,
)
from due.django.testing import WorkerDied
from tests.harness.primitive import (
    PrimitiveOwner,
    PrimitiveWork,
    record,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)


class RejectedBeforeWrite(Exception):
    pass


class Cleanup(IndependentCompensation[PrimitiveWork, PrimitiveWork]):
    def capture(self, producer: WorkClaim[PrimitiveWork]) -> PrimitiveWork:
        return PrimitiveWork(payload=producer.work.payload, universe="acme")

    def compensate(self, work: PrimitiveWork) -> bool | None:
        return None


cleanup = Cleanup(
    model=PrimitiveWork,
    producer_model=PrimitiveWork,
    nudge=lambda identity, token: None,
    policy=DurableWorkPolicy(),
)


class Producer(OwnedDurableWorkCapability[PrimitiveOwner, PrimitiveWork]):
    failure: ClassVar[type[BaseException] | None] = None

    def apply(self, work: PrimitiveWork, context: WorkExecutionContext[PrimitiveWork]) -> None:
        with cleanup.record_cleanup_before_call(context, no_output_on=(RejectedBeforeWrite,)):
            assert PrimitiveWork.objects.filter(owner=None).count() == 1
            if self.failure is not None:
                raise self.failure("provider stopped")


producer = Producer.for_owner(work=PrimitiveWork, owner=PrimitiveOwner, policy=DurableWorkPolicy())


@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("failure", [None, RejectedBeforeWrite, OSError, WorkerDied])
def test_only_declared_no_output_evidence_discharges_compensation(
    failure: type[BaseException] | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = record()
    monkeypatch.setattr(Producer, "failure", failure)
    if failure is None:
        assert producer.attempt_execution(work.pk)
    else:
        with pytest.raises(failure, match="provider stopped"):
            producer.attempt_execution(work.pk)
    output = PrimitiveWork.objects.get(owner=None)
    assert output.state == (DurableWorkState.SUCCEEDED if failure is RejectedBeforeWrite else DurableWorkState.CLAIMED)


class ConfirmedCleanup(Cleanup):
    def compensate(self, work: PrimitiveWork) -> bool:
        # The test application stores its provider completion evidence in payload.
        return work.payload == "writes_finished"


@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("finished", [False, True])
def test_compensation_settles_only_with_application_completion_evidence(finished: bool) -> None:
    capability = ConfirmedCleanup(
        model=PrimitiveWork,
        producer_model=PrimitiveWork,
        nudge=lambda identity, token: None,
        policy=DurableWorkPolicy(max_rearms=None),
    )
    work = PrimitiveWork.objects.create(payload="writes_finished" if finished else "unknown", universe="acme")
    assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == (DurableWorkState.SUCCEEDED if finished else DurableWorkState.READY)
    assert (work.completed_at is not None) == finished


@pytest.mark.usefixtures("isolated_primitive")
def test_compensation_reservation_uses_the_producer_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    from due.django.runtime.coordination import database_now
    from due.django.testing import use_database_clock

    controlled_now = database_now() - timedelta(days=1)
    monkeypatch.setattr(Producer, "failure", None)
    with use_database_clock(lambda: controlled_now):
        work = record()
        assert producer.attempt_execution(work.pk)
        output = PrimitiveWork.objects.get(owner=None)
        assert output.state == DurableWorkState.CLAIMED
        assert output.lease_expires_at == controlled_now + producer.policy.lease_duration
