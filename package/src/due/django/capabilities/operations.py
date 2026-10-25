"""Immutable external service occurrences with evidence-required execution.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from abc import abstractmethod
from typing import ClassVar

from django.db import models, transaction
from django.db.models import Exists, OuterRef

from due.django.capabilities.authoring import OwnedDurableWorkCapability, ReplayContract
from due.django.declarations.mirrors import Outcome
from due.django.evidence.attempts import (
    attempts_for,
    import_unknown_effect_attempt,
    observe_effect_outcome,
    record_effect_attempt,
)
from due.django.evidence.effect_evidence import AppliedEffectEvidenceStore
from due.django.integration import current_universe_scope
from due.django.models import (
    DurableWorkState,
    EffectAttempt,
    EffectAttemptOutcome,
    UniverseDurableWork,
)
from due.django.runtime.coordination import (
    WorkExecutionContext,
    WorkFailureDisposition,
    database_now,
    finalize_under_fence,
    lock_owner_of_work,
    pin_work_beneficiary,
    require_autocommit,
    retry_locked_work,
    settle_locked_work,
    terminal_locked_work,
)
from due.django.runtime.metrics import (
    DurableWorkOperation,
    RecoveryPhase,
    record_work_operation,
)
from due.django.runtime.recovery_pass import RecoveryPass, RecoveryStep
from due.errors import (
    EffectNotAppliedError,
    EffectOutcomeUnknownError,
    EffectRejection,
)
from due.observations import AppliedEffectEvidence, effect_result


class ExternalOperationWork(UniverseDurableWork):
    """One immutable occurrence; unresolved evidence blocks another attempt."""

    requires_effect_evidence = True

    class Meta:
        abstract = True


class DurableExternalOperation[OwnerT: models.Model, WorkT: ExternalOperationWork, ResultT](
    OwnedDurableWorkCapability[OwnerT, WorkT, EffectAttempt]
):
    execution_contract: ClassVar[ReplayContract] = ReplayContract.EVIDENCE_REQUIRED
    rejection_type: ClassVar[type[EffectRejection]] = EffectRejection

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.policy.retention is not None:
            raise ValueError("provider operations retain their occurrence and evidence")
        if self.on_applied is None and type(self).finalize_applied is DurableExternalOperation.finalize_applied:
            raise TypeError("declare on_applied or implement finalize_applied")

    @abstractmethod
    def perform_effect(self, work: WorkT) -> ResultT | AppliedEffectEvidence[ResultT]:
        """Ordinary I/O; raise EffectNotAppliedError only with definite evidence."""

    @property
    @abstractmethod
    def evidence_store(self) -> AppliedEffectEvidenceStore[ResultT]:
        """Declare storage for this operation's canonical provider result."""

    def validate_applied_effect_evidence(self, result: ResultT) -> None:
        """Optional domain constraints beyond the canonical result's schema."""

    def finalize_applied(self, work: WorkT, result: ResultT) -> None:
        """Publish this occurrence's known result inside current owner/work authority."""

    @property
    def on_applied(self) -> Outcome[WorkT, ResultT] | None:
        """Receipt-valued product outcome, before the explicit applied hook."""
        return None

    def _finalize_applied(self, work: WorkT, result: ResultT) -> None:
        if self.on_applied is not None:
            self.on_applied(work, result)
        self.finalize_applied(work, result)
        self._wake_dependents(work)

    def finalize_started(self, work: WorkT) -> None:
        """Optional product transition committed beside the pre-call attempt."""

    def prior_retries(self, owner: OwnerT) -> int:
        """Retries already consumed before this occurrence adopted the runtime."""
        return 0

    def _desired_work(self, *, owner: OwnerT, work: WorkT) -> WorkT:
        desired = super()._desired_work(owner=owner, work=work)
        prior_retries = self.prior_retries(owner)
        if prior_retries < 0:
            raise ValueError("prior retries cannot be negative")
        desired.rearm_count = (
            prior_retries if self.policy.max_rearms is None else min(prior_retries, self.policy.max_rearms)
        )
        return desired

    def record_unknown_effect_outcome(self, work: WorkT) -> None:
        """Import a pre-primitive possible occurrence, never permission to resend it."""
        current = import_unknown_effect_attempt(self.model, work)
        current.state = DurableWorkState.UNKNOWN
        current.last_error_code = "legacy_untracked_publication"
        current.save(update_fields=["state", "last_error_code", "updated_at"])
        finalize_under_fence(current, self._finalize_unknown)

    def _before_attempt_commits(self, work: WorkT, attempt: EffectAttempt) -> None:
        finalize_under_fence(work, self.finalize_started)

    def apply(self, work: WorkT, context: WorkExecutionContext[WorkT]) -> EffectAttempt:
        with context.external_call():
            attempt = record_effect_attempt(context.claim, before_commit=self._before_attempt_commits)
            try:
                result = effect_result(self.perform_effect(work))
            except EffectNotAppliedError as rejection:
                decision = self._record_rejection_or_unknown(attempt, rejection.rejection)
                # Live settlement uses exactly the validated contract recovery reads.
                raise EffectNotAppliedError(decision) from None
            except Exception as error:
                raise EffectOutcomeUnknownError(type(error).__name__) from error
            try:
                self.record_applied_effect_evidence(attempt, result)
            except Exception as error:
                # Provider acceptance without committed evidence remains unknown.
                raise EffectOutcomeUnknownError("provider receipt was not committed") from error
        return attempt

    def _record_rejection_or_unknown(self, attempt: EffectAttempt, rejection: EffectRejection) -> EffectRejection:
        try:
            return self.record_rejection(attempt, rejection)
        except Exception as error:
            raise EffectOutcomeUnknownError("non-application evidence was not committed") from error

    def _lock_evidence_attempt(self, attempt: EffectAttempt) -> EffectAttempt:
        """Positive and negative writers validate the same immutable crossing identity."""
        if attempt.work_model != self.model._meta.label_lower:
            raise ValueError("evidence belongs to another operation model")
        locked = EffectAttempt.objects.select_for_update().get(pk=attempt.pk)
        if (locked.work_model, locked.work_id, locked.desired_revision, locked.claim_token) != (
            attempt.work_model,
            attempt.work_id,
            attempt.desired_revision,
            attempt.claim_token,
        ):
            raise ValueError("persisted evidence identity differs from the supplied attempt")
        return locked

    def record_rejection(self, attempt: EffectAttempt, rejection: EffectRejection) -> EffectRejection:
        """Persist exact negative evidence and its decision atomically, even after fence loss."""
        require_autocommit()
        decision = self.rejection_type.model_validate(rejection.model_dump(mode="json"))
        payload = decision.model_dump(mode="json")
        with transaction.atomic():
            locked = self._lock_evidence_attempt(attempt)
            if locked.outcome == EffectAttemptOutcome.APPLIED:
                raise ValueError("rejection contradicts applied evidence")
            if locked.rejection is not None and locked.rejection != payload:
                raise ValueError("rejection contradicts earlier evidence for this attempt")
            # Persist the decision in the SAME transaction as NOT_APPLIED. A
            # crash can leave neither or both, never replay permission without policy.
            observe_effect_outcome(locked, EffectAttemptOutcome.NOT_APPLIED)
            locked.rejection = payload
            locked.save(update_fields=["rejection"])
        return decision

    def record_applied_effect_evidence(self, attempt: EffectAttempt, result: ResultT) -> None:
        """Accept an exact late receipt without borrowing another worker's token."""
        require_autocommit()
        with transaction.atomic():
            # Evidence lock precedes its domain receipt, consistently for normal
            # and late writers. Product settlement is a separate transaction.
            locked = self._lock_evidence_attempt(attempt)
            if locked.outcome == EffectAttemptOutcome.NOT_APPLIED:
                raise ValueError("applied receipt contradicts definite non-application")
            self.validate_applied_effect_evidence(result)
            self.evidence_store.record(locked, result)
            observe_effect_outcome(locked, EffectAttemptOutcome.APPLIED)

    def record_observed_effect_evidence(self, work_id: int, result: ResultT) -> bool:
        """
        Attach a domain-correlated observation to the sole possible crossing.

        The caller must authenticate and correlate the provider resource with
        this immutable occurrence. This API supplies neither correlation nor
        replay permission. A webhook may precede the RPC reply, but it cannot
        invent a crossing for queued work or borrow a rejected attempt.
        """
        require_autocommit()
        work = self.model.objects.filter(pk=work_id).first()
        if work is None:
            return False
        attempt = self.possible_effect_attempt(work)
        if attempt is None:
            return False
        self.record_applied_effect_evidence(attempt, result)
        return True

    def possible_effect_attempt(self, work: WorkT) -> EffectAttempt | None:
        """
        Read this occurrence's sole possible provider entry, including settled APPLIED.

        None means no entry or only definite rejections, NOT that queued work
        cannot enter later. The attempt's started_at is the pre-call entry time,
        not proof the provider received it. This snapshot supplies no lock,
        cancellation or replay permission. More than one possible crossing is
        an invariant violation; never choose the latest by timestamp.
        """
        if not isinstance(work, self.model):
            raise ValueError("work belongs to another operation model")
        try:
            return (
                attempts_for(work)
                .exclude(outcome=EffectAttemptOutcome.NOT_APPLIED)
                .get(desired_revision=work.desired_revision)
            )
        except EffectAttempt.DoesNotExist:
            return None

    def finalize_success(self, work: WorkT, result: EffectAttempt, /) -> None:
        # A webhook can commit newer evidence before the RPC returns. Live and
        # recovery settlement must consume the same canonical stored receipt.
        self._finalize_applied(work, self.evidence_store.read(result))
        EffectAttempt.objects.filter(pk=result.pk).update(repair_pending=False)

    def classify_error(self, error: Exception) -> WorkFailureDisposition:
        if isinstance(error, EffectOutcomeUnknownError):
            return WorkFailureDisposition.UNKNOWN
        if isinstance(error, EffectNotAppliedError):
            return error.rejection.disposition
        return WorkFailureDisposition.RETRYABLE

    def derivation_steps(self) -> tuple[RecoveryStep, ...]:
        return (
            *super().derivation_steps(),
            RecoveryStep(RecoveryPhase.EVIDENCE_SETTLEMENT, self._settle_applied_evidence),
            RecoveryStep(RecoveryPhase.NONAPPLICATION, self._reconcile_nonapplication_page),
        )

    def _settle_applied_evidence(self, recovery: RecoveryPass) -> int:
        """Settle work whose APPLIED receipt still owes product repair, one work id at a time."""
        applied_work = EffectAttempt.objects.filter(
            work_model=self.model._meta.label_lower,
            outcome=EffectAttemptOutcome.APPLIED,
            repair_pending=True,
        ).values("work_id")
        return recovery.run_page(
            RecoveryPhase.EVIDENCE_SETTLEMENT,
            self.model.objects.filter(pk__in=applied_work).order_by("pk").values_list("pk", flat=True),
            self.policy.batch_size,
            self.settle_from_applied_effect_evidence,
        )

    def _reconcile_nonapplication_page(self, recovery: RecoveryPass) -> int:
        """Retry UNKNOWN work whose every attempt is proven NOT_APPLIED, one work id at a time."""
        evidence = EffectAttempt.objects.filter(work_model=self.model._meta.label_lower, work_id=OuterRef("pk"))
        return recovery.run_page(
            RecoveryPhase.NONAPPLICATION,
            self.model.objects.filter(state=DurableWorkState.UNKNOWN)
            .filter(Exists(evidence.filter(outcome=EffectAttemptOutcome.NOT_APPLIED)))
            .filter(~Exists(evidence.exclude(outcome=EffectAttemptOutcome.NOT_APPLIED)))
            .order_by("pk")
            .values_list("pk", flat=True),
            self.policy.batch_size,
            self.reconcile_nonapplication,
        )

    def settle_expired_claim(self, work: WorkT) -> bool:
        return self._settle_rejection_locked(work)

    def _settle_rejection_locked(self, work: WorkT) -> bool:
        """Caller holds owner/work locks; only this execution's decision may settle."""
        evidence = attempts_for(work)
        if evidence.exclude(outcome=EffectAttemptOutcome.NOT_APPLIED).exists():
            return False
        try:
            attempt = evidence.get(
                desired_revision=work.desired_revision,
                claim_token=work.fencing_token,
                rejection__isnull=False,
            )
        except EffectAttempt.DoesNotExist:
            return False  # Historical untyped evidence keeps its existing policy.
        error = EffectNotAppliedError(self.rejection_type.model_validate(attempt.rejection))
        disposition = self.classify_error(error)
        with current_universe_scope(work.work_universe()):
            if disposition is WorkFailureDisposition.TERMINAL:
                terminal_locked_work(
                    work,
                    failed_at=database_now(),
                    error_code="effect_rejected",
                    finalize=lambda current: self._finalize_terminal(current, error),
                )
                operation = DurableWorkOperation.TERMINAL_FAILED
            elif disposition is WorkFailureDisposition.RETRYABLE:
                retry_locked_work(
                    work,
                    retry_at=database_now(),
                    max_rearms=self.policy.max_rearms,
                    # Measured from settlement, not from the refusal: recovery
                    # can only wait longer than the provider asked, never less.
                    retry_delay=max(self.retry_delay_for(work), self.provider_retry_after(error)),
                    error_code="effect_rejected",
                    finalize=lambda current: self._finalize_retry(current, error),
                )
                operation = (
                    DurableWorkOperation.EXHAUSTED
                    if work.state == DurableWorkState.EXHAUSTED
                    else DurableWorkOperation.RETRYABLE
                )
            else:
                raise ValueError("a definite rejection must select retry or terminal settlement")
            pin_work_beneficiary(work)
        record_work_operation(self.model._meta.label_lower, operation)
        return True

    def reconcile_nonapplication(self, work_id: int) -> bool:
        """Unblock only after every possible attempt is definitely not applied."""
        require_autocommit()
        with transaction.atomic():
            lock_owner_of_work(self.model, work_id)
            work = self.model.objects.select_for_update().filter(pk=work_id).first()
            if work is None:
                return False
            evidence = attempts_for(work)
            if work.state != DurableWorkState.UNKNOWN or not evidence.exists():
                return False
            if evidence.exclude(outcome=EffectAttemptOutcome.NOT_APPLIED).exists():
                return False
            if self._settle_rejection_locked(work):
                return True
            with current_universe_scope(work.work_universe()):
                retry_locked_work(
                    work,
                    retry_at=database_now(),
                    max_rearms=self.policy.max_rearms,
                    retry_delay=self.retry_delay_for(work),
                    error_code="late_definite_nonapplication",
                    finalize=lambda current: self._finalize_retry(
                        current, EffectNotAppliedError("Late evidence: not published")
                    ),
                )
        record_work_operation(
            self.model._meta.label_lower,
            DurableWorkOperation.EXHAUSTED
            if work.state == DurableWorkState.EXHAUSTED
            else DurableWorkOperation.RETRYABLE,
        )
        return True

    def settle_from_applied_effect_evidence(self, work_id: int) -> bool:
        """Complete from durable evidence under fresh authority, without another call."""
        require_autocommit()
        with transaction.atomic():
            lock_owner_of_work(self.model, work_id)
            work = self.model.objects.select_for_update().filter(pk=work_id).first()
            if work is None:
                return False
            try:
                attempt = attempts_for(work).get(
                    desired_revision=work.desired_revision, outcome=EffectAttemptOutcome.APPLIED, repair_pending=True
                )
            except EffectAttempt.DoesNotExist:
                return False
            # No timestamp-based latest-attempt selection. More than one possible
            # applied attempt is an invariant violation, not permission to guess.
            result = self.evidence_store.read(attempt)
            self.validate_applied_effect_evidence(result)
            with current_universe_scope(work.work_universe()):
                settle_locked_work(
                    work,
                    revision=work.desired_revision,
                    finalize=lambda current: self._finalize_applied(current, result),
                )
            EffectAttempt.objects.filter(pk=attempt.pk).update(repair_pending=False)
        return True
