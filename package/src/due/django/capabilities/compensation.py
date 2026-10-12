"""Independent cleanup obligations for potentially produced output.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from abc import abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar

from django.db import transaction

from due.django.capabilities.authoring import DurableWorkCapability
from due.django.models import DurableWorkState, UniverseDurableWork
from due.django.runtime.coordination import (
    ContinueAfter,
    WorkClaim,
    WorkExecutionContext,
    WorkOwnershipLost,
    claim_work,
    database_now,
    lock_work_owner,
    require_fenced_settlement,
    settle_locked_work,
)


@dataclass(frozen=True)
class ProtectedOutput[SourceT: UniverseDurableWork, CleanupT: UniverseDurableWork]:
    """Exact attempt evidence, not another source of product state."""

    _producer: WorkClaim[SourceT]
    _cleanup: WorkClaim[CleanupT]

    def confirm_no_output(self) -> None:
        """The provider definitely created no output and has no pending write."""
        self._acknowledge()

    def confirm_compensated(self) -> None:
        """All producer writes finished and their required compensation succeeded."""
        self._acknowledge()

    def confirm_published(self, producer: SourceT) -> None:
        """Keep output only in its exact producer's current product settlement."""
        require_fenced_settlement(producer)
        if (
            type(producer) is not self._producer.model
            or producer.pk != self._producer.work_id
            or producer.fencing_token != self._producer.token
            or producer.desired_revision != self._producer.desired_revision
        ):
            raise WorkOwnershipLost("output belongs to another producer settlement")
        self._acknowledge()

    def _acknowledge(self) -> None:
        # This is definite output evidence, not a cleanup worker acknowledgement.
        # A reaped cleanup lease cannot erase it. A cleanup already in flight
        # loses its ordinary continuation/settlement fence when this commits.
        model = self._cleanup.model
        with transaction.atomic():
            current = model.objects.select_for_update().filter(pk=self._cleanup.work_id).first()
            if current is None or current.state == DurableWorkState.SUCCEEDED:
                return  # Already acknowledged and possibly retention-pruned.
            if current.desired_revision != self._cleanup.desired_revision:
                raise WorkOwnershipLost("compensation identity was superseded")
            settle_locked_work(current, revision=self._cleanup.desired_revision)


@dataclass(frozen=True)
class IndependentCompensation[SourceT: UniverseDurableWork, CleanupT: UniverseDurableWork](
    DurableWorkCapability[CleanupT]
):
    producer_model: type[SourceT]
    inspection_interval: timedelta = timedelta(days=1)
    # The producer holds the cleanup claim through ProtectedOutput, which settles
    # only that exact revision; a wake would fence the claim and break settlement.
    wakeable: ClassVar[bool] = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.inspection_interval <= timedelta(0):
            raise ValueError("uncertain output needs a positive inspection interval")

    @abstractmethod
    def capture(self, producer: WorkClaim[SourceT]) -> CleanupT:
        """Build an unsaved row capturing this attempt's exact output identity."""

    @abstractmethod
    def compensate(self, work: CleanupT) -> bool | None:
        """
        Compensate output; return True only with evidence that all producer writes finished.

        None or False retains uncertainty and schedules another inspection.
        Absence on a read and local lease expiry are not completion evidence.
        """

    @contextmanager
    def record_cleanup_before_call(
        self, context: WorkExecutionContext[SourceT], *, no_output_on: tuple[type[Exception], ...] = ()
    ) -> Iterator[ProtectedOutput[SourceT, CleanupT]]:
        if context.claim.model is not self.producer_model:
            raise TypeError("cleanup is registered for another producer model")
        # Entry guard, capture and reservation finish before ordinary provider I/O.
        with context.external_call():
            producer = context.claim
            with transaction.atomic():
                lock_work_owner(producer.work)
                current = (
                    producer.model.objects.select_for_update()
                    .filter(
                        pk=producer.work_id,
                        state=DurableWorkState.CLAIMED,
                        fencing_token=producer.token,
                        desired_revision=producer.desired_revision,
                    )
                    .first()
                )
                if current is None or current.lease_expires_at is None or current.lease_expires_at <= database_now():
                    raise WorkOwnershipLost("producer lost ownership before cleanup recording")
                # Keep exact claim identity, but account for an owner detached by
                # SET_NULL while this worker was waiting to record its output.
                current_producer = WorkClaim(
                    current, producer.token, producer.claim_generation, producer.desired_revision
                )
                work = self.capture(current_producer)
                if type(work) is not self.model or not work._state.adding:
                    raise ValueError("capture must return a new row of the registered cleanup model")
                work.universe = producer.work.work_universe()
                work.available_at = database_now()
                work.save(force_insert=True)
                cleanup = claim_work(self.model, work.pk, lease_duration=context.lease_duration)
                assert cleanup is not None, "new cleanup must be reserved in its creation transaction"
            output = ProtectedOutput(producer, cleanup)
            try:
                yield output
            except no_output_on:
                # The producer declares only errors proven to precede every
                # write. All other exceptions (including worker death) retain
                # independent compensation; an absent object is not evidence.
                output.confirm_no_output()
                raise
        # No implicit success on context exit: output still needs publication or
        # definite compensation. Death/exception leaves independently due work.

    def apply(self, work: CleanupT, context: WorkExecutionContext[CleanupT]) -> ContinueAfter | None:
        with context.external_call():
            finished = self.compensate(work)
        if finished:
            return None
        # Absence now is not proof that an earlier admitted write cannot land.
        return ContinueAfter(self.inspection_interval, until=None)
