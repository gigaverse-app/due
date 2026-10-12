"""Replaceable deadlines and cancellation over durable effects.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from abc import abstractmethod
from datetime import datetime, timedelta
from enum import StrEnum
from typing import ClassVar, final
from uuid import UUID

from django.db import models

from due.django.capabilities._attempt_recovery import attempt_recovery_steps
from due.django.capabilities.authoring import (
    OwnedDurableWorkCapability,
    _validate_simple_callbacks,
)
from due.django.evidence.attempts import (
    applied_effect_call,
    attempts_for,
)
from due.django.models import (
    ACTIVE_WORK_STATES,
    DurableWorkState,
    EffectAttemptOutcome,
    RepairSettledBy,
    UniverseDurableWork,
)
from due.django.runtime.coordination import (
    ContinueAfter,
    WorkExecutionContext,
    require_fenced_settlement,
    settle_locked_work,
)
from due.django.runtime.recovery_pass import RecoveryStep
from due.observations import AppliedEffectEvidence, effect_result


class ScheduledWork(UniverseDurableWork):
    """A deadline is desired input; available_at also includes retry backoff."""

    run_at = models.DateTimeField()
    cancelled = models.BooleanField(default=False)

    class Meta:
        abstract = True


class ScheduleResult(StrEnum):
    SCHEDULED = "scheduled"
    UNCHANGED = "unchanged"
    ALREADY_STARTED = "already_started"


class CancellationResult(StrEnum):
    CANCELLED = "cancelled"
    NOT_SCHEDULED = "not_scheduled"
    ALREADY_STARTED = "already_started"


class DurableScheduledAction[OwnerT: models.Model, WorkT: ScheduledWork](
    OwnedDurableWorkCapability[OwnerT, WorkT, bool]
):
    """
    Record or replace a deadline now; run a replay-safe action when due.

    Cancellation prevents entry, not remote execution. A durable attempt is
    committed before the ordinary provider call. Cancellation and replacement
    refuse to promise prevention once any attempt might have escaped.
    """

    supports_repair_window: ClassVar[bool] = True
    attempt_retention: ClassVar[timedelta] = timedelta(days=7)

    def _deadline_candidates(self) -> models.QuerySet[WorkT]:
        return super()._deadline_candidates().filter(cancelled=False)

    def derivation_steps(self) -> tuple[RecoveryStep, ...]:
        return attempt_recovery_steps(
            self.model,
            self.policy,
            retention=self.attempt_retention,
            derivation=super().derivation_steps(),
            keep_started_evidence=True,
        )

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.input_fields is not None and not {"run_at", "cancelled"}.issubset(self.input_fields):
            raise ValueError("scheduled input_fields must include run_at and cancelled")
        if self.policy.retention is not None:
            raise ValueError("scheduled actions retain cancellation and attempt identity")

    def _has_started(self, work: WorkT) -> bool:
        return (
            attempts_for(work)
            .filter(desired_revision=work.desired_revision)
            .exclude(outcome=EffectAttemptOutcome.NOT_APPLIED)
            .exists()
        )

    def schedule_at(
        self,
        owner_id: int | str | UUID,
        work: WorkT | None = None,
        *,
        at: datetime,
        replace_pending: bool = True,
        **values: object,
    ) -> ScheduleResult:
        """Persist deferred work; replace pending input, or report ALREADY_STARTED."""
        with self.atomic(owner_id) as pending:
            if work is None:
                work = pending.capture_work(**values)
            elif values:
                raise TypeError("pass either an explicit work model or captured field overrides")
            work.run_at = at
            work.cancelled = False
            existing = pending.existing_work
            desired = self._desired_work(owner=pending.locked_owner, work=work)
            if existing is not None:
                if self._same_domain_state(existing, desired):
                    return ScheduleResult.UNCHANGED
                if self._has_started(existing):
                    return ScheduleResult.ALREADY_STARTED
                if not replace_pending and existing.state in ACTIVE_WORK_STATES:
                    return ScheduleResult.UNCHANGED
            pending.request_work(work, not_before=at)
        return ScheduleResult.SCHEDULED

    def cancel_pending(self, owner_id: int | str | UUID) -> CancellationResult:
        """Invalidate pending delivery now; never pretend to abort escaped I/O."""
        with self.atomic(owner_id, notify_worker=False) as pending:
            existing = pending.existing_work
            if existing is None or existing.cancelled:
                return CancellationResult.NOT_SCHEDULED
            if self._has_started(existing):
                return CancellationResult.ALREADY_STARTED
            if existing.state == DurableWorkState.SUCCEEDED:
                return CancellationResult.NOT_SCHEDULED
            desired = self.model(**{field.attname: field.value_from_object(existing) for field in self.domain_fields})
            desired.cancelled = True
            current = pending.request_work(desired)
            settle_locked_work(current, revision=current.desired_revision)
        return CancellationResult.CANCELLED

    @abstractmethod
    def is_still_needed(self, work: WorkT) -> bool | ContinueAfter:
        """Read current domain facts before entry; raise on uncertain reads."""

    @abstractmethod
    def perform_effect(self, work: WorkT) -> None | AppliedEffectEvidence[None]:
        """Ordinary replay-safe I/O; a normal return is definite success."""

    def apply(self, work: WorkT, context: WorkExecutionContext[WorkT]) -> bool | ContinueAfter:
        if work.cancelled:
            return False
        needed = self.is_still_needed(work)
        if isinstance(needed, ContinueAfter) or not needed:
            return needed
        with applied_effect_call(context):
            effect_result(self.perform_effect(work))
        return True

    def finalize_success(self, work: WorkT, result: bool, /) -> None:
        # Replay can satisfy this deadline without proving an earlier timed-out
        # request finished. Preserve UNKNOWN for resource reuse gates.
        if result:
            attempts_for(work).filter(
                desired_revision=work.desired_revision, outcome=EffectAttemptOutcome.APPLIED
            ).update(repair_pending=False, repair_settled_by=RepairSettledBy.RECEIPT)


class ActionOutcome(StrEnum):
    """Product meaning of a completed deadline, without exposing receipt internals."""

    PERFORMED = "performed"
    NO_LONGER_NEEDED = "no_longer_needed"


class ScheduledAction[OwnerT: models.Model, WorkT: ScheduledWork](DurableScheduledAction[OwnerT, WorkT]):
    """
    Run a deadline with an ordinary, fenced on_completed product callback.

    The callback also receives NO_LONGER_NEEDED when cancellation or eligibility
    prevented entry. It returns None; the runtime settles confirmed receipts
    and retains earlier uncertainty. DurableScheduledAction remains the rich API.
    """

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_simple_callbacks(self, ScheduledAction)

    def on_completed(self, work: WorkT, outcome: ActionOutcome) -> None:
        """Optionally update product rows atomically with deadline settlement."""

    @final
    def _finalize_success(self, work: WorkT, result: bool) -> None:
        require_fenced_settlement(work)
        outcome = ActionOutcome.PERFORMED if result else ActionOutcome.NO_LONGER_NEEDED
        if self.on_completed(work, outcome) is not None:
            raise TypeError("on_completed must return None; receipt settlement is runtime-owned")
        DurableScheduledAction.finalize_success(self, work, result)
        self._wake_dependents(work)
