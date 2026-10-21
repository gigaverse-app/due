"""Desired-state projection with retained evidence for escaped old writes.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar, final
from uuid import UUID

from django.db import connection, models, transaction
from django.db.models import Exists, OuterRef, Q

from due.django.capabilities._attempt_recovery import attempt_recovery_steps
from due.django.capabilities.authoring import (
    DurableWorkPolicy,
    OwnedDurableWorkCapability,
    _validate_simple_callbacks,
)
from due.django.evidence.attempts import (
    applied_effect_call,
    attempts_for,
    import_unknown_effect_attempt,
    publish_pending_attempt_census,
    retire_repair_for_gone_target,
)
from due.django.models import (
    TERMINAL_WORK_STATES,
    DurableWorkState,
    EffectAttempt,
    EffectAttemptOutcome,
    RepairSettledBy,
    UniverseDurableWork,
)
from due.django.runtime.coordination import (
    ContinueAfter,
    ReopenAfter,
    WorkExecutionContext,
    database_now,
    reopen_locked_work,
    require_fenced_settlement,
    supersede_work,
)
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import RecoveryPass, RecoveryStep


def projections_with_stale_writes[WorkT: UniverseDurableWork](
    work: models.QuerySet[WorkT], *, window: timedelta | None = None
) -> models.QuerySet[WorkT]:
    """
    Select projections that an escaped older revision could still overwrite.

    Same-revision uncertainty remains retained for resource-reuse decisions and
    future revisions, but cannot change a successfully applied current payload.
    """
    stale = EffectAttempt.objects.filter(
        work_model=work.model._meta.label_lower,
        work_id=OuterRef("pk"),
        repair_pending=True,
        desired_revision__lt=OuterRef("desired_revision"),
    )
    if window is not None:
        stale = stale.exclude(outcome=EffectAttemptOutcome.UNKNOWN, started_at__lt=database_now() - window)
    return work.filter(Exists(stale))


def settle_projection_observation[WorkT: UniverseDurableWork](
    work: WorkT,
    *,
    since: datetime,
    policy: DurableWorkPolicy,
    repair_interval: timedelta = timedelta(minutes=5),
    applied_attempt: EffectAttempt | None = None,
) -> ReopenAfter | None:
    """Settle a current projection observed over one complete, possibly paginated pass.

    ``since`` precedes every read/write in that pass. A late write observed after
    it still owes repair: completing the last page cannot vouch for earlier pages.
    Call only after the entire desired view has been established, under its fence.
    """
    require_fenced_settlement(work)
    covered = Q(outcome=EffectAttemptOutcome.APPLIED, observed_at__lte=since)
    if applied_attempt is not None:
        covered |= Q(pk=applied_attempt.pk)
    attempts_for(work).filter(covered).update(repair_pending=False, repair_settled_by=RepairSettledBy.RECEIPT)
    model: type[WorkT] = type(work)
    if projections_with_stale_writes(model.objects.filter(pk=work.pk), window=policy.max_repair_window).exists():
        return ReopenAfter(repair_interval, "stale_write_repair")
    return None


def projection_recovery_steps[WorkT: UniverseDurableWork](
    model: type[WorkT],
    policy: DurableWorkPolicy,
    *,
    derivation: tuple[RecoveryStep, ...],
    retention: timedelta = timedelta(days=7),
) -> tuple[RecoveryStep, ...]:
    """Reuse projection repair for checkpointed writers without a second recovery owner.

    Mutating calls must use ``applied_effect_call``. A reopened paginated writer
    must start a fresh pass; its previous cursor cannot repair already-read pages.
    """

    owner = ProjectionRecovery(model, policy)
    return (
        *attempt_recovery_steps(
            model,
            policy,
            retention=retention,
            derivation=(*derivation, RecoveryStep(RecoveryPhase.STALE_WRITE_REOPEN, owner.reopen_stale_writes)),
        ),
        RecoveryStep(RecoveryPhase.ATTEMPT_CENSUS, owner.census),
    )


@dataclass(frozen=True)
class ProjectionRecovery:
    """Retain the model/policy binding of the shared checkpointed-writer factory."""

    model: type[UniverseDurableWork]
    policy: DurableWorkPolicy

    def reopen_stale_writes(self, recovery: RecoveryPass) -> int:
        # Late receipts can arrive after the new revision was acknowledged.
        # Exhausted rows remain visible; recovery never grants a new retry budget.
        with transaction.atomic():
            page = list(
                projections_with_stale_writes(
                    self.model.objects.select_for_update(skip_locked=True), window=self.policy.max_repair_window
                )
                .filter(state=DurableWorkState.SUCCEEDED)
                .order_by("completed_at", "pk")[: self.policy.batch_size]
            )
            available_at = database_now()
            for work in page:
                reopen_locked_work(work, available_at=available_at, reason="stale_write_repair")
        return len(page)

    def census(self, recovery: RecoveryPass) -> int:
        publish_pending_attempt_census(self.model)
        return 0


class DurableProjection[OwnerT: models.Model, WorkT: UniverseDurableWork](
    OwnedDurableWorkCapability[OwnerT, WorkT, EffectAttempt]
):
    """
    Write latest desired state; retain and repair potentially late writes.

    ``write_projection`` performs ordinary provider I/O. A normal return means
    the requested projection was applied (including provider-proven absence).
    Errors and worker death retain UNKNOWN evidence. There is no inferred remote
    cancellation from lease expiry or local process termination.
    """

    supports_repair_window: ClassVar[bool] = True
    attempt_retention: ClassVar[timedelta] = timedelta(days=7)

    @property
    def uncertain_repair_interval(self) -> timedelta:
        return timedelta(minutes=5)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.policy.retention is not None:
            raise ValueError("projections retain work identity while late-write evidence can refer to it")

    @abstractmethod
    def write_projection(self, work: WorkT) -> None:
        """Apply this immutable desired revision through the canonical provider port."""

    def retire_escaped_writes(self, work: WorkT) -> int:
        """
        Settle older-revision repair debt once the provider target is gone for good.

        Call from write_projection only with definitive provider evidence that the
        captured target no longer exists and cannot return: never an outage, a
        failed read or elapsed time. Safe from a stale executor, because a gone
        target stays gone. Returns the number of attempts settled.
        """
        return retire_repair_for_gone_target(work)

    def record_unknown_effect_outcome(self, work: WorkT) -> None:
        """
        Retain a possible pre-primitive write of an unknown older document.

        Call once inside initial owner/work admission, after stopping legacy
        producers. Import grants no remote ordering guarantee: the existing
        projection repair policy owns late overwrites, even after first success.
        Unlike an occurrence import, current desired work remains READY to run.
        """
        import_unknown_effect_attempt(self.model, work, prior_revision=True)

    def apply(self, work: WorkT, context: WorkExecutionContext[WorkT]) -> EffectAttempt | ContinueAfter:
        if (
            self.policy.max_repair_window is not None
            and work.applied_revision == work.desired_revision
            and not projections_with_stale_writes(
                self.model.objects.filter(pk=work.pk), window=self.policy.max_repair_window
            ).exists()
        ):
            # A repair can already be queued when its old uncertainty expires.
            # Reuse definite evidence for the current revision; normal fenced
            # finalization rechecks late receipts before declaring it settled.
            confirmed = (
                attempts_for(work)
                .filter(desired_revision=work.desired_revision, outcome=EffectAttemptOutcome.APPLIED)
                .order_by("-started_at", "-pk")
                .first()
            )
            if confirmed is not None:
                return confirmed
        with applied_effect_call(context) as attempt:
            self.write_projection(work)
        return attempt

    def finalize_success(self, work: WorkT, result: EffectAttempt, /) -> ReopenAfter | None:
        return settle_projection_observation(
            work,
            since=result.started_at,
            policy=self.policy,
            repair_interval=self.uncertain_repair_interval,
            applied_attempt=result,
        )

    def derivation_steps(self) -> tuple[RecoveryStep, ...]:
        return projection_recovery_steps(
            self.model, self.policy, retention=self.attempt_retention, derivation=super().derivation_steps()
        )

    def reopen_work(self, owner_id: int | str | UUID) -> None:
        """
        Operator repair: re-read current desired state and restart finished work.

        Active work keeps its token and budget. Finished or exhausted work gets
        a fresh revision and budget; unresolved old attempt evidence survives.
        Only the database is changed here. Provider execution is deferred.
        """
        self._reschedule(owner_id, replace_active=False)

    def refresh_in_worker(self, owner_id: int | str | UUID) -> None:
        """
        Record a changed source now, invalidating old work; run after commit.

        Call inside the product mutation's transaction for changes whose fields
        are read from the canonical owner rather than copied into the work row.
        """
        if not connection.in_atomic_block:
            raise RuntimeError("change the source and replace its projection inside the same transaction.atomic()")
        self._reschedule(owner_id, replace_active=True)

    def _reschedule(self, owner_id: int | str | UUID, *, replace_active: bool) -> None:
        if self.discovery is None:
            raise TypeError("operator reconciliation requires canonical desired-state derivation")
        with self.atomic(owner_id) as pending:
            desired = self.discovery.desired_for(pending.locked_owner, pending.existing_work)
            if desired is None:
                return
            existing = pending.existing_work
            if (
                existing is not None
                and (replace_active or existing.state in TERMINAL_WORK_STATES)
                and self._same_domain_state(existing, self._desired_work(owner=pending.locked_owner, work=desired))
            ):
                supersede_work(self.model, existing.pk)
                existing.refresh_from_db()
            pending.request_work(desired)


class Projection[OwnerT: models.Model, WorkT: UniverseDurableWork](DurableProjection[OwnerT, WorkT]):
    """
    Project desired state and optionally update product rows in on_projected.

    The callback runs under the owner/work fence, in the same transaction as
    receipt settlement. It returns None and never calls super(). The runtime
    retains any late-write repair. Use DurableProjection when an application
    needs to own the richer settlement protocol explicitly.
    """

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_simple_callbacks(self, Projection)

    def on_projected(self, work: WorkT) -> None:
        """Optionally publish product state after the desired write is confirmed."""

    @final
    def _finalize_success(self, work: WorkT, result: EffectAttempt) -> ReopenAfter | None:
        repair = DurableProjection.finalize_success(self, work, result)
        if self.on_projected(work) is not None:
            raise TypeError("on_projected must return None; projection repair is runtime-owned")
        self._wake_dependents(work)
        return repair
