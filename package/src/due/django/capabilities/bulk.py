"""Batches on Due's work-table lifecycle: one work row per fixed cohort, one provider call per round.

``DurableBulkExternalOperation`` is for calls that must not be repeated when
their outcome is uncertain: every crossing captures its exact members in a
receipt beside or inside EffectAttempt. ``DurableBulkEffect`` is for idempotent calls:
an uncertain round is simply resent for its pending members. Item progress is
bounded JSON, never one work row per item. Claims, fencing, retry budgets and
recovery remain core-owned. Verdict semantics and reducers live in ``due.bulk``.
"""

from abc import abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from functools import cached_property, partial
from typing import ClassVar, final
from uuid import UUID

from django.contrib.postgres.fields import ArrayField
from django.db import connection, models, transaction
from django.db.models import F, Func, OuterRef, Q, QuerySet, Subquery, Value
from pydantic import BaseModel, JsonValue, TypeAdapter

from due.bulk import (
    BulkOutcome,
    BulkOutcomeState,
    BulkResult,
    BulkRetryPolicy,
    BulkStep,
    RejectionResponse,
    RetryLimit,
    advance_progress,
    batch_state,
    merge_bulk_evidence,
    pending_members,
    run_bulk,
)
from due.django.capabilities.authoring import OwnedDurableWorkCapability
from due.django.capabilities.operations import DurableExternalOperation, ExternalOperationWork
from due.django.evidence.attempts import attempts_for, locked_effect_attempt, observe_locked_effect_outcome
from due.django.evidence.effect_evidence import AppliedEffectEvidenceStore, StoredAppliedEffectEvidence
from due.django.identities import WorkId
from due.django.models import DurableWork, DurableWorkState, EffectAttempt, EffectAttemptOutcome, UniverseDurableWork
from due.django.runtime.coordination import (
    ContinueAfter,
    WorkExecutionContext,
    WorkFailureDisposition,
    WorkOwnershipLost,
    _PreparedWorkUpdates,
    database_now,
    finalize_under_fence,
    lock_work_owner,
    renew_work,
    require_autocommit,
    require_fenced_settlement,
    retry_locked_work,
    settle_locked_work,
    unknown_locked_work,
)
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import RecoveryStep, ends_the_tick
from due.django.runtime.settlement_batch import _SettlementBatch
from due.django.runtime.settlement_writes import finish_receipt
from due.errors import EffectNotAppliedError, EffectOutcomeUnknownError, EffectRejection


class BulkWork(models.Model):
    """A fixed cohort of string member keys and its committed per-member progress."""

    bulk_keys = models.JSONField()
    bulk_progress = models.JSONField(default=list, blank=True)

    class Meta:
        abstract = True


class BulkEffectWork(BulkWork, UniverseDurableWork):
    """A cohort whose provider calls are idempotent; adopted by DurableBulkEffect."""

    class Meta:
        abstract = True


class BulkExternalOperationWork(BulkWork, ExternalOperationWork):
    """One fixed cohort; only definitively rejected members may enter again."""

    class Meta:
        abstract = True

    def _possible_effect_attempts(self) -> QuerySet[EffectAttempt]:
        # Unlike a scalar occurrence, a batch may make progress over several
        # calls. An APPLIED attempt stops blocking ONLY when its per-item
        # progress commits in the same transaction as repair_pending=False.
        # New crossings select only that progress's definitely unsent subset.
        return (
            attempts_for(self)
            .exclude(outcome=EffectAttemptOutcome.NOT_APPLIED)
            .exclude(outcome=EffectAttemptOutcome.APPLIED, repair_pending=False)
        )


class StoredBulkEffectEvidence(StoredAppliedEffectEvidence):
    """Exact pre-call membership plus monotonic, incrementally observed evidence."""

    members = models.JSONField()
    result = models.JSONField(default=list)
    settlement_pending = models.BooleanField(default=False)

    class Meta:
        abstract = True


def bulk_evidence_index(name: str) -> models.Index:
    """Concrete receipt models include this index for shared receipt recovery."""
    return models.Index(fields=["attempt"], condition=Q(settlement_pending=True), name=name)


@dataclass(frozen=True)
class BulkEvidenceStore[ResultT: BaseModel](AppliedEffectEvidenceStore[BulkResult[ResultT]]):
    model: type[StoredBulkEffectEvidence] | None
    result_type: type[ResultT]

    @cached_property
    def adapter(self) -> TypeAdapter[BulkResult[ResultT]]:
        return TypeAdapter(BulkResult[self.result_type])

    def read(self, attempt: EffectAttempt) -> BulkResult[ResultT]:
        if self.model is None:
            return self._read_inline(EffectAttempt.objects.only("receipt").get(pk=attempt.pk))
        receipt = self.model.objects.get(attempt=attempt)
        recorded = self.adapter.validate_python({"items": receipt.result})
        return merge_bulk_evidence(receipt.members, recorded, BulkResult(items=()))

    def _read_inline(self, attempt: EffectAttempt) -> BulkResult[ResultT]:
        if attempt.receipt is None:
            raise EffectAttempt.DoesNotExist("the crossing has no captured inline receipt")
        recorded = self.adapter.validate_python({"items": attempt.receipt["result"]})
        return merge_bulk_evidence(attempt.receipt["members"], recorded, BulkResult(items=()))

    def read_locked(self, attempt: EffectAttempt) -> BulkResult[ResultT]:
        return self._read_inline(attempt) if self.model is None else self.read(attempt)

    def _stage_inline(
        self, attempt: EffectAttempt, result: BulkResult[ResultT]
    ) -> tuple[BulkResult[ResultT], tuple[str, ...]]:
        if attempt.receipt is None:
            raise EffectAttempt.DoesNotExist("the crossing has no captured inline receipt")
        # Merge the stored fragment directly. Expanding missing members to
        # UNKNOWN first constructs verdicts the incoming fragment may replace.
        recorded = self.adapter.validate_python({"items": attempt.receipt["result"]})
        retained = merge_bulk_evidence(attempt.receipt["members"], recorded, self.adapter.validate_python(result))
        encoded = self.adapter.dump_python(retained, mode="json")["items"]
        fields: list[str] = []
        receipt = dict(attempt.receipt)
        if encoded != receipt["result"]:
            receipt["result"] = encoded
            attempt.receipt_pending = True
            fields.extend(("receipt", "receipt_pending"))
        if result.diagnostics is not None and result.diagnostics != receipt.get("diagnostics"):
            # The attempt lock serializes diagnostic and late-evidence writers.
            # Metadata never changes replay permission or settlement debt.
            receipt["diagnostics"] = result.diagnostics
            if "receipt" not in fields:
                fields.append("receipt")
        if fields:
            attempt.receipt = receipt
        return retained, tuple(fields)

    def record_applied(
        self,
        attempt: EffectAttempt,
        result: BulkResult[ResultT],
        *,
        complete: Callable[[BulkResult[ResultT]], bool] | None = None,
    ) -> BulkResult[ResultT] | None:
        if self.model is not None:
            if result.diagnostics is not None:
                raise ValueError(
                    "BulkResult diagnostics require inline receipts; use the custom receipt's metadata API"
                )
            return super().record_applied(attempt, result, complete=complete)
        retained, fields = self._stage_inline(attempt, result)
        if complete is None or complete(retained):
            observe_locked_effect_outcome(attempt, EffectAttemptOutcome.APPLIED, extra_fields=fields)
        elif fields:
            attempt.save(update_fields=fields)
        return retained

    def record(self, attempt: EffectAttempt, result: BulkResult[ResultT]) -> None:
        self.record_and_read(attempt, result)

    def record_and_read(self, attempt: EffectAttempt, result: BulkResult[ResultT]) -> BulkResult[ResultT]:
        # DurableExternalOperation holds the attempt lock for both owner and
        # late writers. Membership was committed BEFORE provider entry.
        if self.model is None:
            retained, fields = self._stage_inline(attempt, result)
            if fields:
                attempt.save(update_fields=fields)
            return retained
        receipt = self.model.objects.get(attempt=attempt)
        incoming = self.adapter.validate_python(result)
        first = self.adapter.validate_python({"items": receipt.result})
        retained = merge_bulk_evidence(receipt.members, first, incoming)
        encoded = self.adapter.dump_python(retained, mode="json")["items"]
        if encoded != receipt.result:
            receipt.result = encoded
            receipt.settlement_pending = True
            receipt.save(update_fields=["result", "settlement_pending"])
        # The attempt lock serializes every writer. This merged value is the
        # canonical receipt just persisted, including earlier partial evidence.
        return retained

    def retains(self, stored: BulkResult[ResultT], received: BulkResult[ResultT]) -> bool:
        by_key = {item.key: item for item in stored.items}
        return all(item.state is BulkOutcomeState.UNKNOWN or by_key.get(item.key) == item for item in received.items)

    def member_expression(self) -> F | Subquery:
        if self.model is None:
            return F("receipt__members")
        return Subquery(self.model.objects.filter(attempt_id=OuterRef("pk")).values("members"))

    def members(self, attempt: EffectAttempt) -> tuple[str, ...]:
        if self.model is None:
            self._read_inline(attempt)  # Missing/corrupt receipt is not an empty membership.
            assert attempt.receipt is not None
            return tuple(attempt.receipt["members"])
        return tuple(self.model.objects.get(attempt=attempt).members)

    def pending_attempts(self, work_model: str) -> QuerySet[EffectAttempt]:
        attempts = EffectAttempt.objects.filter(work_model=work_model)
        if self.model is None:
            return attempts.filter(receipt_pending=True)
        return attempts.filter(pk__in=self.model.objects.filter(settlement_pending=True).values("attempt_id"))

    def consume(self, attempt: EffectAttempt, state: BulkOutcomeState) -> None:
        # The caller owns a freshly loaded attempt lock. Progress and these
        # replay/settlement markers commit in the same fenced transaction.
        fields = []
        repair_pending = state is not BulkOutcomeState.RETRYABLE
        if attempt.repair_pending != repair_pending:
            attempt.repair_pending = repair_pending
            fields.append("repair_pending")
        if self.model is None and state is not BulkOutcomeState.COMPLETE and attempt.receipt_pending:
            attempt.receipt_pending = False
            fields.append("receipt_pending")
        if fields:
            attempt.save(update_fields=fields)
        if self.model is not None and state is not BulkOutcomeState.COMPLETE:
            self.model.objects.filter(attempt=attempt).update(settlement_pending=False)

    def finish(self, attempt: EffectAttempt) -> None:
        # Core completion owns the work fence; never trust a pre-I/O snapshot
        # of the attempt's markers when completing its retained evidence.
        finish_receipt(attempt, self.model)


@dataclass(frozen=True)
class CapturedBatch:
    attempt_id: UUID
    keys: tuple[str, ...]


class BulkRetryNeeded(RuntimeError):
    """Definite remaining work exhausted its bounded inline rounds."""


@dataclass(frozen=True)
class _BatchProgress[WorkT: BulkEffectWork | BulkExternalOperationWork, ResultT: BaseModel]:
    """Declaration checks and progress reads shared by both bulk capabilities."""

    result_type: type[ResultT] = field(kw_only=True)
    bulk_policy: BulkRetryPolicy = field(default=BulkRetryPolicy(1, timedelta(seconds=60)), kw_only=True)

    @cached_property
    def progress_adapter(self) -> TypeAdapter[BulkResult[ResultT]]:
        return TypeAdapter(BulkResult[self.result_type])

    def _progress(self, work: WorkT) -> BulkResult[ResultT]:
        return self.progress_adapter.validate_python({"items": work.bulk_progress})

    def completed_items(self, work: WorkT) -> tuple[BulkOutcome[ResultT], ...]:
        return tuple(item for item in self._progress(work).items if item.state is BulkOutcomeState.COMPLETE)

    def pending_keys(self, work: WorkT) -> tuple[str, ...]:
        return pending_members(work.bulk_keys, self._progress(work))

    def progress_updates(self, work: WorkT, progress: BulkResult[ResultT]) -> Mapping[str, object]:
        """Project accumulated progress onto application fields on this work row.

        Return values only; do not save or perform I/O. Due persists them with
        bulk_progress under the same fence, before finalize_batch. Only concrete
        non-relation fields outside declared inputs and coordination are allowed.
        The default leaves existing adopters' finalizers unchanged.
        """
        return {}

    def _record_progress(
        self, work: WorkT, result: BulkResult[ResultT], *, input_fields: tuple[str, ...] | None
    ) -> BulkOutcomeState:
        """Commit one call's verdicts to the cohort; the caller holds the fenced transaction."""
        current, fields = self._prepare_progress(work, result, input_fields=input_fields)
        # Preserve the write barrier: finalizers can query the saved projection.
        work.save(update_fields=fields)
        return batch_state(work.bulk_keys, current)

    def _prepare_progress(
        self, work: WorkT, result: BulkResult[ResultT], *, input_fields: tuple[str, ...] | None
    ) -> tuple[BulkResult[ResultT], tuple[str, ...]]:
        """Validate and apply the transition in memory under the caller's fence."""
        require_fenced_settlement(work)
        current = advance_progress(work.bulk_keys, self._progress(work), result)
        work.bulk_progress = self.progress_adapter.dump_python(current, mode="json")["items"]
        updates = dict(self.progress_updates(work, current))
        if updates:
            assert input_fields is not None, "bulk input_fields are checked at capability construction"
            protected = (
                {field.name for field in DurableWork._meta.concrete_fields} | set(input_fields) | {"bulk_progress"}
            )
            allowed = {
                field.name
                for field in work._meta.concrete_fields
                if not field.primary_key and not field.is_relation and field.name not in protected
            }
            if invalid := updates.keys() - allowed:
                raise ValueError(f"progress_updates must name application progress fields: {sorted(invalid)}")
            for name, value in updates.items():
                setattr(work, name, value)
        return current, ("bulk_progress", "updated_at", *updates)

    @staticmethod
    def _check_declaration(
        *, input_fields: tuple[str, ...] | None, bulk_policy: BulkRetryPolicy, lease_duration: timedelta
    ) -> None:
        if input_fields is None or "bulk_keys" not in input_fields or "bulk_progress" in input_fields:
            raise ValueError("bulk input_fields must include bulk_keys and exclude evidence-owned bulk_progress")
        if bulk_policy.max_rounds < 1 or bulk_policy.wall_clock_budget <= timedelta(0):
            raise ValueError("bulk round and elapsed budgets must be positive")
        # Each round renews the lease before it sleeps and again before the
        # provider call, so one backoff sleep is the longest unrenewed interval.
        if bulk_policy.delay is not None and bulk_policy.delay.maximum >= lease_duration:
            raise ValueError("a bulk backoff sleep must be shorter than the work lease")

    @staticmethod
    def _check_new_batch(work: BulkWork) -> None:
        if not work.bulk_keys or any(not isinstance(key, str) or not key for key in work.bulk_keys):
            raise ValueError("a batch requires nonempty string member keys")
        if len(set(work.bulk_keys)) != len(work.bulk_keys):
            raise ValueError("batch member keys must be unique")
        if work.bulk_progress:
            raise ValueError("new batch progress is owned by evidence settlement")


@dataclass(frozen=True)
class DurableBulkExternalOperation[
    OwnerT: models.Model,
    WorkT: BulkExternalOperationWork,
    ResultT: BaseModel,
    PayloadT = None,
](_BatchProgress[WorkT, ResultT], DurableExternalOperation[OwnerT, WorkT, BulkResult[ResultT]]):
    """One provider call per round for a fixed cohort, with the round's request built before entry.

    Adopters implement ``prepare_batch`` (render the request; nothing reaches
    the provider) and ``send_batch`` (one provider call, a verdict per member).
    A whole-call definite rejection is ``EffectNotAppliedError`` from either
    hook, never a result with every member retryable: the attempt then records
    NOT_APPLIED and ``on_batch_rejected`` chooses inline retry, a pause or the
    core retry policy for its members.
    """

    receipt_model: ClassVar[type[StoredBulkEffectEvidence] | None]
    observation_key: ClassVar[str] = "bulk_observation"
    # Opt-in: applied hooks/dependents use the in-memory projection, without
    # querying or writing its fields before the completion save. Success hooks
    # still observe the persisted projection and SUCCEEDED state.
    combine_completed_progress: ClassVar[bool] = False

    def attempt_many(
        self, work_ids: Iterable[WorkId], *, settlement_batch_size: int = 8, set_based: bool = False
    ) -> dict[WorkId, bool]:
        """Attempt independent self-owned rows, sharing bounded settlement commits.

        Experimental opt-in: successful receipts commit separately before local
        completion is buffered. Progress becomes visible at a batch boundary or
        when the iterable ends. The bound counts rows, not elapsed time.

        Existing model saves, signals and completion hooks still run per row.
        Hooks must be transaction-local/replay-safe and must not mutate another
        row in this batch. A failed group rolls back and retries settlement per
        row; failed rows keep their receipts for ordinary recovery, without a
        provider resend. Ordinary provider failures stop iteration after flushing
        earlier receipts. Process death leaves those receipts for recovery.

        No claim, provider call, evidence commit or retry policy is batched. The
        returned booleans have attempt_execution's meaning after all flushes.
        Existing single-row APIs keep their synchronous behavior.

        set_based additionally combines work updates and receipt-debt clearing.
        Opt in only for independent transaction-local hooks: preparation cannot
        rely on a peer's completion, and finalizers must not read receipt-debt
        markers or peer progress. Success timestamps share a statement instant.
        Custom model persistence, fields or save signals retain ordinary saves.
        This changes neither receipt durability nor permission to resend.
        """
        require_autocommit()
        if isinstance(settlement_batch_size, bool) or not isinstance(settlement_batch_size, int):
            raise TypeError("settlement_batch_size must be an integer")
        if not 1 <= settlement_batch_size <= 64:
            raise ValueError("settlement_batch_size must be between 1 and 64")
        if self.model.work_owner_field is not None or self.deadline_wakeup_horizon is not None:
            raise ValueError("bounded settlement requires self-owned work without deadline notifications")
        if self.wakes and settlement_batch_size > 1:
            # Waking takes each dependent row's lock last in its transaction. A group
            # would interleave several rows' dependent locks in arrival order, so two
            # groups sharing dependents could deadlock.
            raise ValueError("bounded settlement cannot group work that wakes dependents")
        if self.model.objects.db != connection.alias:
            raise ValueError("bounded settlement requires the default database")
        if not isinstance(set_based, bool):
            raise TypeError("set_based must be a boolean")
        batch = _SettlementBatch[WorkT](settlement_batch_size, set_based=set_based)
        outcomes: dict[WorkId, bool] = {}
        try:
            for identity in work_ids:
                outcomes[identity] = self._attempt_execution(identity, completion=batch.add)
                if batch.full:
                    batch.flush()
        except Exception as error:
            if ends_the_tick(error):
                raise
            try:
                batch.flush()
            except Exception as settlement_error:
                raise ExceptionGroup("execution and bounded settlement failed", [error, settlement_error]) from None
            raise
        batch.flush()
        outcomes.update(batch.outcomes)
        return outcomes

    def __post_init__(self) -> None:
        super().__post_init__()
        self._check_declaration(
            input_fields=self.input_fields, bulk_policy=self.bulk_policy, lease_duration=self.policy.lease_duration
        )
        if not issubclass(self.model, BulkExternalOperationWork):
            raise TypeError("bulk operations require BulkExternalOperationWork")

    @cached_property
    def evidence_store(self) -> BulkEvidenceStore[ResultT]:
        return BulkEvidenceStore(self.receipt_model, self.result_type)

    def _desired_work(self, *, owner: OwnerT, work: WorkT) -> WorkT:
        self._check_new_batch(work)
        return super()._desired_work(owner=owner, work=work)

    def _before_attempt_commits(self, work: WorkT, attempt: EffectAttempt) -> Callable[[WorkT], BulkResult[ResultT]]:
        if self.receipt_model is not None:
            receipt = self.receipt_model.objects.create(attempt=attempt, members=self._crossing_members(work))
            members = receipt.members
        else:
            if attempt.receipt is None:
                raise EffectAttempt.DoesNotExist("the crossing has no captured inline receipt")
            members = attempt.receipt["members"]
        batch = CapturedBatch(attempt.pk, tuple(members))
        super()._before_attempt_commits(work, attempt)
        return lambda current: self._perform_batch(current, batch)

    def _crossing_members(self, work: WorkT) -> list[str]:
        members = self.pending_keys(work)
        if not members:
            raise ValueError("a provider batch must contain pending members")
        return list(members)

    def _initial_effect_receipt(self, work: WorkT) -> JsonValue:
        if self.receipt_model is None:
            return {"members": [key for key in self._crossing_members(work)], "result": []}
        return None

    def current_batch(self, work: WorkT) -> CapturedBatch:
        """The exact captured membership and correlation ID of the open crossing."""
        # Select attempts first: an extra UNKNOWN attempt must remain ambiguous
        # even if its receipt is missing. An inner receipt join would hide it.
        attempt = (
            attempts_for(work)
            .annotate(captured_members=self.evidence_store.member_expression())
            .values("pk", "captured_members")
            .get(claim_token=work.fencing_token, outcome=EffectAttemptOutcome.UNKNOWN, repair_pending=True)
        )
        if attempt["captured_members"] is None:
            raise (self.receipt_model or EffectAttempt).DoesNotExist("the open crossing has no captured membership")
        return CapturedBatch(attempt["pk"], tuple(attempt["captured_members"]))

    def record_receipt_diagnostics(self, attempt_id: UUID, diagnostics: JsonValue) -> None:
        """Retain opaque diagnostics without changing semantic evidence or replay permission.

        Inline receipts keep this optional metadata beside membership and
        verdicts. A database expression preserves concurrent canonical writes;
        the attempt row serializes both writers. Custom receipt models retain
        their own diagnostic fields and APIs.
        """
        if self.receipt_model is not None:
            raise ValueError("inline diagnostics require receipt_model=None")
        updated = EffectAttempt.objects.filter(
            pk=attempt_id, work_model=self.model._meta.label_lower, receipt__isnull=False
        ).update(
            receipt=Func(
                F("receipt"),
                Value(["diagnostics"], output_field=ArrayField(models.TextField())),
                Value(diagnostics, output_field=models.JSONField()),
                function="jsonb_set",
                output_field=models.JSONField(),
            )
        )
        if updated != 1:
            raise EffectAttempt.DoesNotExist("no inline receipt belongs to this capability and attempt")

    @abstractmethod
    def prepare_batch(self, work: WorkT, batch: CapturedBatch) -> PayloadT:
        """Build this round's request before provider entry.

        Nothing here may reach the provider. Raise EffectNotAppliedError for a
        definite decision; any other exception is a retryable preparation
        failure, recorded as definite nonapplication of this crossing.
        """

    @abstractmethod
    def send_batch(self, work: WorkT, batch: CapturedBatch, payload: PayloadT) -> BulkResult[ResultT]:
        """Make exactly one provider call and return a verdict for every captured member."""

    @final
    def perform_effect(self, work: WorkT) -> BulkResult[ResultT]:
        return self._perform_batch(work, self.current_batch(work))

    def _perform_batch(self, work: WorkT, batch: CapturedBatch) -> BulkResult[ResultT]:
        try:
            payload = self.prepare_batch(work, batch)
        except EffectNotAppliedError:
            raise
        except Exception as error:
            # Preparation precedes provider entry, so it proves nonapplication.
            raise EffectNotAppliedError(
                EffectRejection(message=f"batch preparation failed: {type(error).__name__}")
            ) from error
        return self.send_batch(work, batch, payload)

    @abstractmethod
    def finalize_batch(self, work: WorkT, result: BulkResult[ResultT]) -> None:
        """Publish provider-specific member consequences under Due's current fence."""

    def finalize_rejection(self, work: WorkT, rejection: EffectRejection) -> None:
        """Publish a whole-call rejection's product consequence once, live or in recovery, under the fence."""

    def on_batch_rejected(self, work: WorkT, rejection: EffectRejection) -> RejectionResponse | ContinueAfter:
        """Choose the response to a live retryable whole-call rejection.

        ContinueAfter pauses without spending the failure budget, for example
        until a provider's rate-limit reset. Recovery of a rejection whose
        worker died always uses the core retry policy.
        """
        return RejectionResponse.RETRY_LATER

    def before_batch(self, work: WorkT) -> ContinueAfter | None:
        """Recheck product eligibility before every crossing; a pause spends no retry budget."""
        return None

    def observe_batch(
        self, work: WorkT, attempt: EffectAttempt, members: tuple[str, ...]
    ) -> BulkResult[ResultT] | None:
        """Look up evidence for an uncertain crossing (receipts, a provider task). None means nothing new."""
        return None

    def finalize_applied(self, work: WorkT, result: BulkResult[ResultT]) -> None:
        self.finalize_batch(work, result)

    def record_applied_effect_evidence(
        self, attempt: EffectAttempt, result: BulkResult[ResultT]
    ) -> BulkResult[ResultT] | None:
        return self._record_effect_evidence(attempt, result, complete=lambda retained: retained.complete)

    def validate_rejection_evidence(self, attempt: EffectAttempt, rejection: EffectRejection) -> None:
        if any(item.state is BulkOutcomeState.COMPLETE for item in self.evidence_store.read(attempt).items):
            raise ValueError("whole-batch rejection contradicts a definite member result")

    def blocking_attempt_filter(self) -> Q:
        return ~Q(outcome=EffectAttemptOutcome.APPLIED, repair_pending=False)

    def possible_effect_attempt(self, work: WorkT) -> EffectAttempt | None:
        if not isinstance(work, self.model):
            raise ValueError("work belongs to another operation model")
        try:
            return (
                attempts_for(work)
                .filter(self.blocking_attempt_filter())
                .exclude(outcome=EffectAttemptOutcome.NOT_APPLIED)
                .get()
            )
        except EffectAttempt.DoesNotExist:
            return None

    def evidence_requiring_settlement(self) -> QuerySet[EffectAttempt]:
        return self.evidence_store.pending_attempts(self.model._meta.label_lower)

    def classify_error(self, error: Exception) -> WorkFailureDisposition:
        if isinstance(error, BulkRetryNeeded):
            return WorkFailureDisposition.RETRYABLE
        return super().classify_error(error)

    def _consume_evidence(
        self, work: WorkT, attempt: EffectAttempt, result: BulkResult[ResultT], *, completing: bool = False
    ) -> BulkOutcomeState:
        require_fenced_settlement(work)
        state = self._record_progress(work, result, input_fields=self.input_fields)
        self._finalize_applied(work, result)
        if completing:
            if state is not BulkOutcomeState.COMPLETE:
                raise ValueError("bulk work cannot complete with unfinished members")
            # Core completion clears both markers in this same transaction.
            # Publishing an intermediate owed marker cannot help recovery here;
            # rollback retains the independently committed receipt and debt.
            return state
        # INVARIANT: replay permission and member progress commit together.
        # An uncertain member retains the entire exact attempt for later receipts.
        # The last receipt still owes final work settlement. Keeping its repair
        # marker until finalize_success covers death after progress commits but
        # before the core completes the claim, without inventing another send.
        self.evidence_store.consume(attempt, state)
        return state

    def apply(self, work: WorkT, context: WorkExecutionContext[WorkT]) -> EffectAttempt | ContinueAfter:
        last: EffectAttempt | None = None
        continuation: ContinueAfter | None = None

        def after_rejection(rejected: EffectNotAppliedError) -> BulkStep:
            nonlocal continuation
            # A provider floor uses the shared durable retry path and budget.
            # Inline rounds and product pauses cannot shorten that deadline.
            if self.provider_retry_after(rejected) > timedelta(0):
                raise rejected
            response = self.on_batch_rejected(work, rejected.rejection)
            if response is RejectionResponse.RETRY_LATER:
                raise rejected
            with transaction.atomic():
                lock_work_owner(work)
                current = context.claim.locked_work()
                if current is None:
                    # The NOT_APPLIED decision is committed; recovery settles it
                    # through the same rejection path, exactly once.
                    raise rejected
                finalize_under_fence(current, self._consume_rejections)
            work.refresh_from_db()
            continuation = response if isinstance(response, ContinueAfter) else self.before_batch(work)
            return BulkStep.STOP if continuation is not None else BulkStep.RETRY

        def step() -> BulkStep:
            nonlocal last, continuation
            continuation = self.before_batch(work)
            if continuation is not None:
                return BulkStep.STOP
            # Reuse the scalar crossing: current claim guard, pre-I/O committed
            # EffectAttempt, classified rejection and canonical receipt commit.
            try:
                crossing, recorded = super(DurableBulkExternalOperation, self)._apply_with_evidence(work, context)
            except EffectNotAppliedError as rejected:
                if rejected.rejection.disposition is WorkFailureDisposition.TERMINAL:
                    raise
                return after_rejection(rejected)
            last = crossing
            # The writer returns only after committing its canonical merge.
            # Unknown members can still gain late evidence and must be reloaded.
            retained = recorded
            if recorded is None:  # Legacy/custom writers need not return evidence.
                recorded = self.evidence_store.read(crossing)
            predicted = advance_progress(work.bulk_keys, self._progress(work), recorded)
            if batch_state(work.bulk_keys, predicted) is BulkOutcomeState.COMPLETE:
                if (
                    retained is not None
                    and retained.complete
                    and self.combine_completed_progress
                    and type(self.evidence_store) is BulkEvidenceStore
                    and type(self).record_applied_effect_evidence
                    is DurableBulkExternalOperation.record_applied_effect_evidence
                    and type(self)._prepare_success is DurableBulkExternalOperation._prepare_success
                ):
                    # Definite outcomes for this exact membership cannot change:
                    # the store rejects contradictions. Keep an independent copy
                    # so provider-owned mutable models cannot alter committed facts.
                    # This replaces only preparation, never the fresh work fence.
                    context._success.result = crossing
                    context._success.prepare = partial(
                        self._prepare_committed_success, result=retained.model_copy(deep=True)
                    )
                return BulkStep.STOP
            with transaction.atomic():
                lock_work_owner(work)
                current = context.claim.locked_work()
                if current is None:
                    # Match scalar execution: the core completion declines this
                    # stale claim; the committed receipt remains owed to recovery.
                    return BulkStep.STOP
                attempt = locked_effect_attempt(last.pk)
                result = self.evidence_store.read_locked(attempt)
                state = finalize_under_fence(current, lambda row: self._consume_evidence(row, attempt, result))
            # Finished batches return to core completion, which reacquires the
            # work lock and reads current progress. No next round needs this
            # transient snapshot. Retry and unknown paths keep their refresh.
            if state is BulkOutcomeState.COMPLETE:
                return BulkStep.STOP
            work.refresh_from_db()
            if state is BulkOutcomeState.UNKNOWN:
                raise EffectOutcomeUnknownError("bulk members remain unknown")
            continuation = self.before_batch(work)
            return BulkStep.STOP if continuation is not None else BulkStep.RETRY

        def exhausted(reason: RetryLimit) -> None:
            raise BulkRetryNeeded(reason.value)

        run_bulk(
            step=step,
            ready=lambda: bool(self.pending_keys(work)),
            resume=lambda: renew_work(context.claim, lease_duration=context.lease_duration),
            exhausted=exhausted,
            policy=self.bulk_policy,
        )
        if continuation is not None:
            return continuation
        if last is None:
            raise WorkOwnershipLost("bulk execution stopped without a settled crossing")
        return last

    def _prepare_completed_progress(self, work: WorkT, result: BulkResult[ResultT]) -> _PreparedWorkUpdates:
        current, fields = self._prepare_progress(work, result, input_fields=self.input_fields)
        if batch_state(work.bulk_keys, current) is not BulkOutcomeState.COMPLETE:
            raise ValueError("bulk work cannot complete with unfinished members")
        self._finalize_applied(work, result)
        return _PreparedWorkUpdates(fields)

    def _prepare_success(self, work: WorkT, result: EffectAttempt) -> _PreparedWorkUpdates | None:
        require_fenced_settlement(work)
        if batch_state(work.bulk_keys, self._progress(work)) is BulkOutcomeState.COMPLETE:
            return
        attempt = locked_effect_attempt(result.pk)
        recorded = self.evidence_store.read_locked(attempt)
        if self.combine_completed_progress:
            return self._prepare_completed_progress(work, recorded)
        self._consume_evidence(work, attempt, recorded, completing=True)
        return None

    def _prepare_committed_success(self, work: WorkT, *, result: BulkResult[ResultT]) -> _PreparedWorkUpdates | None:
        require_fenced_settlement(work)
        if batch_state(work.bulk_keys, self._progress(work)) is BulkOutcomeState.COMPLETE:
            return None
        return self._prepare_completed_progress(work, result)

    def finalize_success(self, work: WorkT, result: EffectAttempt, /) -> None:
        if batch_state(work.bulk_keys, self._progress(work)) is not BulkOutcomeState.COMPLETE:
            raise ValueError("bulk work cannot complete with unfinished members")
        self.evidence_store.finish(result)
        self._wake_dependents(work)

    def _unconsumed_rejections(self, work: WorkT) -> list[EffectAttempt]:
        """This claim's NOT_APPLIED crossings whose members are not yet released for retry."""
        rejected = (
            attempts_for(work)
            .select_for_update()
            .filter(
                claim_token=work.fencing_token,
                desired_revision=work.desired_revision,
                outcome=EffectAttemptOutcome.NOT_APPLIED,
            )
        )
        return [attempt for attempt in rejected if not self.evidence_store.read(attempt).complete]

    def rejection_awaiting_settlement(self, work: WorkT) -> EffectAttempt | None:
        # Inline rounds may leave several settled rejections under one claim;
        # only one crossing can be open, so at most one still owes a decision.
        pending = [attempt for attempt in self._unconsumed_rejections(work) if attempt.rejection is not None]
        if len(pending) > 1:
            raise ValueError("a bulk claim has more than one unsettled rejection")
        return pending[0] if pending else None

    def _consume_rejections(self, work: WorkT) -> None:
        """Release a rejected crossing's unresolved members for retry and publish its consequence."""
        require_fenced_settlement(work)
        for attempt in self._unconsumed_rejections(work):
            recorded = self.evidence_store.read(attempt)
            resolved = BulkResult(
                items=tuple(
                    item.model_copy(update={"state": BulkOutcomeState.RETRYABLE})
                    if item.state is BulkOutcomeState.UNKNOWN
                    else item
                    for item in recorded.items
                )
            )
            self.evidence_store.record(attempt, resolved)
            self._consume_evidence(work, attempt, resolved)
            if attempt.rejection is not None:
                self.finalize_rejection(work, self.rejection_type.model_validate(attempt.rejection))

    def _finalize_retry(self, work: WorkT, error: Exception) -> None:
        if isinstance(error, EffectNotAppliedError):
            self._consume_rejections(work)
        super()._finalize_retry(work, error)

    def _finalize_terminal(self, work: WorkT, error: Exception) -> None:
        if isinstance(error, EffectNotAppliedError):
            self._consume_rejections(work)
        super()._finalize_terminal(work, error)

    def derivation_steps(self) -> tuple[RecoveryStep, ...]:
        steps = super().derivation_steps()
        if type(self).observe_batch is DurableBulkExternalOperation.observe_batch:
            return steps
        observation = RecoveryStep.paged(
            RecoveryPhase.OBSERVATION,
            lambda: (
                self.model.objects.filter(state=DurableWorkState.UNKNOWN).order_by("pk").values_list("pk", flat=True)
            ),
            self.observe_uncertain_batch,
            size=self.policy.batch_size,
            key=self.observation_key,
        )
        return (observation, *steps)

    def observe_uncertain_batch(self, work_id: WorkId) -> int:
        """Record newly observed evidence; the evidence-settlement phase applies it."""
        work = self.model.objects.filter(pk=work_id, state=DurableWorkState.UNKNOWN).first()
        if work is None:
            return 0
        attempt = self.possible_effect_attempt(work)
        if attempt is None:
            return 0
        members = self.evidence_store.members(attempt)
        result = self.observe_batch(work, attempt, members)
        if result is None:
            return 0
        self.record_applied_effect_evidence(attempt, result)
        return 1

    def _settle_evidence_locked(self, work: WorkT, attempt: EffectAttempt, result: BulkResult[ResultT]) -> None:
        prepared = None
        if (
            self.combine_completed_progress
            and batch_state(work.bulk_keys, advance_progress(work.bulk_keys, self._progress(work), result))
            is BulkOutcomeState.COMPLETE
        ):
            prepared = finalize_under_fence(work, lambda row: self._prepare_completed_progress(row, result))
            state = BulkOutcomeState.COMPLETE
        else:
            state = finalize_under_fence(work, lambda row: self._consume_evidence(row, attempt, result))
        if state is BulkOutcomeState.UNKNOWN:
            unknown_locked_work(work, error_code="bulk_members_unknown", finalize=self._finalize_unknown)
        elif state is BulkOutcomeState.COMPLETE:
            settle_locked_work(
                work,
                revision=work.desired_revision,
                finalize=lambda row: self._finalize_success(row, attempt),
                prepared=prepared,
            )
        else:
            retry_locked_work(
                work,
                retry_at=database_now(),
                max_rearms=self.policy.max_rearms,
                retry_delay=self.retry_delay_for(work),
                error_code="bulk_members_rejected",
                finalize=lambda row: self._finalize_retry(row, BulkRetryNeeded("late definite rejection")),
            )


@dataclass(frozen=True)
class DurableBulkEffect[
    OwnerT: models.Model,
    WorkT: BulkEffectWork,
    ResultT: BaseModel,
    PayloadT = None,
](_BatchProgress[WorkT, ResultT], OwnedDurableWorkCapability[OwnerT, WorkT, BulkResult[ResultT]]):
    """One idempotent provider call per round for a fixed cohort, with no attempt or receipt rows.

    Use this only when resending a member the provider may already have applied
    is harmless, for example adding chat members or upserting users. A call
    whose outcome is uncertain raises any exception: the core retry policy then
    resends every member without a definite verdict. ``send_batch`` returns
    COMPLETE or RETRYABLE for each member it sent; a whole-call definite
    rejection is ``EffectNotAppliedError``, answered by ``on_batch_rejected``.
    """

    def __post_init__(self) -> None:
        super().__post_init__()
        self._check_declaration(
            input_fields=self.input_fields, bulk_policy=self.bulk_policy, lease_duration=self.policy.lease_duration
        )
        if not issubclass(self.model, BulkEffectWork) or self.model.requires_effect_evidence:
            raise TypeError("replay-safe bulk effects require BulkEffectWork without effect evidence")

    def _desired_work(self, *, owner: OwnerT, work: WorkT) -> WorkT:
        self._check_new_batch(work)
        return super()._desired_work(owner=owner, work=work)

    @abstractmethod
    def prepare_batch(self, work: WorkT, keys: tuple[str, ...]) -> PayloadT:
        """Build this round's request for the pending members, without provider I/O."""

    @abstractmethod
    def send_batch(self, work: WorkT, keys: tuple[str, ...], payload: PayloadT) -> BulkResult[ResultT]:
        """Make exactly one idempotent provider call; return COMPLETE or RETRYABLE for every key."""

    @abstractmethod
    def finalize_batch(self, work: WorkT, result: BulkResult[ResultT]) -> None:
        """Publish one call's member verdicts under Due's current fence."""

    def before_batch(self, work: WorkT) -> ContinueAfter | None:
        """Recheck product eligibility before every call; a pause spends no retry budget."""
        return None

    def on_batch_rejected(self, work: WorkT, rejection: EffectRejection) -> RejectionResponse | ContinueAfter:
        """Choose the response to a retryable whole-call rejection, e.g. pause until a rate-limit reset."""
        return RejectionResponse.RETRY_LATER

    def classify_error(self, error: Exception) -> WorkFailureDisposition:
        if isinstance(error, EffectNotAppliedError):
            return error.rejection.disposition
        return WorkFailureDisposition.RETRYABLE

    def apply(self, work: WorkT, context: WorkExecutionContext[WorkT]) -> BulkResult[ResultT] | ContinueAfter:
        continuation: ContinueAfter | None = None

        def step() -> BulkStep:
            nonlocal continuation
            continuation = self.before_batch(work)
            if continuation is not None:
                return BulkStep.STOP
            keys = self.pending_keys(work)
            payload = self.prepare_batch(work, keys)
            try:
                with context.external_call():
                    result = self.send_batch(work, keys, payload)
            except EffectNotAppliedError as rejected:
                if rejected.rejection.disposition is WorkFailureDisposition.TERMINAL:
                    raise
                if self.provider_retry_after(rejected) > timedelta(0):
                    raise
                response = self.on_batch_rejected(work, rejected.rejection)
                if response is RejectionResponse.RETRY_LATER:
                    raise
                continuation = response if isinstance(response, ContinueAfter) else self.before_batch(work)
                return BulkStep.STOP if continuation is not None else BulkStep.RETRY
            if {item.key for item in result.items} != set(keys) or len(result.items) != len(keys):
                raise ValueError("a replay-safe batch result must give exactly one verdict per sent member")
            if not result.complete:
                raise ValueError("replay-safe batches raise for uncertainty instead of returning UNKNOWN")
            with transaction.atomic():
                lock_work_owner(work)
                current = context.claim.locked_work()
                if current is None:
                    raise WorkOwnershipLost("bulk progress lost its claim; recovery resends pending members")
                state = finalize_under_fence(current, lambda row: self._commit_round(row, result))
            work.refresh_from_db()
            if state is BulkOutcomeState.COMPLETE:
                return BulkStep.STOP
            continuation = self.before_batch(work)
            return BulkStep.STOP if continuation is not None else BulkStep.RETRY

        def exhausted(reason: RetryLimit) -> None:
            raise BulkRetryNeeded(reason.value)

        run_bulk(
            step=step,
            ready=lambda: bool(self.pending_keys(work)),
            resume=lambda: renew_work(context.claim, lease_duration=context.lease_duration),
            exhausted=exhausted,
            policy=self.bulk_policy,
        )
        if continuation is not None:
            return continuation
        return self._progress(work)

    def _commit_round(self, work: WorkT, result: BulkResult[ResultT]) -> BulkOutcomeState:
        state = self._record_progress(work, result, input_fields=self.input_fields)
        self.finalize_batch(work, result)
        return state
