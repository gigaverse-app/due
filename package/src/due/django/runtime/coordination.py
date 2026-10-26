"""Claim, execution and settlement kernel for work-table effects.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from functools import partial
from typing import Any, TypeVar, cast
from uuid import UUID, uuid4

from django.db import connection, models, transaction
from django.db.models import Count, F, Min, Q, QuerySet

from due.django.declarations.eligibility import ExecutionEligibility
from due.django.declarations.external_services import execution_external_services
from due.django.identities import WorkId
from due.django.integration import current_universe_scope, pin_after_write
from due.django.models import (
    ATTENTION_WORK_STATES,
    TERMINAL_WORK_STATES,
    DurableWork,
    DurableWorkState,
)
from due.django.runtime._delivery import dispatch_recovery_page as _dispatch_recovery_page
from due.django.runtime.counters import saturating_increment
from due.django.runtime.metrics import (
    DurableWorkOperation,
    RecoveryPhase,
    record_execution_eligibility,
    record_outstanding,
    record_work_census,
    record_work_operation,
)
from due.django.runtime.recovery_pass import RecoveryPass, recovery_pass_for
from due.errors import WorkFailureDisposition

logger = logging.getLogger(__name__)

WorkT = TypeVar("WorkT", bound=DurableWork)
ResultT = TypeVar("ResultT")
NowFn = Callable[[], datetime]
Publisher = Callable[[int, UUID], object] | Callable[[UUID, UUID], object]


def publish_work(publish: Publisher, identity: WorkId, token: UUID) -> object:
    """Invoke the publisher bound to this model's selected primary-key type.

    Preserve integer-only callback annotations for existing integer adopters.
    The model determines which callback contract applies, not a text coercion.
    """
    return cast(Callable[[WorkId, UUID], object], publish)(identity, token)


CLAIMABLE_STATES = (
    DurableWorkState.READY,
    DurableWorkState.RETRYABLE,
)
ACTIVE_STATES = (
    DurableWorkState.READY,
    DurableWorkState.QUEUED,
    DurableWorkState.CLAIMED,
    DurableWorkState.RETRYABLE,
    DurableWorkState.UNKNOWN,
)


@dataclass(frozen=True, slots=True)
class ContinueAfter:
    """Wait without spending the failure budget; ``until=None`` explicitly keeps watching."""

    delay: timedelta
    until: datetime | None

    def __post_init__(self) -> None:
        if self.delay <= timedelta(0):
            raise ValueError("continuation delay must be positive")
        if self.until is not None and self.until.utcoffset() is None:
            raise ValueError("continuation deadline must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ReopenAfter:
    """Current inputs were applied, but retained evidence requires another repair."""

    delay: timedelta
    reason: str

    def __post_init__(self) -> None:
        if self.delay <= timedelta(0):
            raise ValueError("repair delay must be positive")
        if not self.reason or len(self.reason) > 64:
            raise ValueError("repair reason must contain 1 to 64 characters")


@dataclass(frozen=True, slots=True)
class WorkClaim[ClaimedWorkT: DurableWork]:
    """
    Immutable ownership facts committed by one successful claim.

    The model instance is the typed input snapshot handed to the handler. The
    fence facts are copied separately so refreshing or otherwise mutating that
    instance cannot make a stalled worker adopt its successor's ownership.
    """

    work: ClaimedWorkT
    token: UUID
    claim_generation: int
    desired_revision: int

    @property
    def work_id(self) -> WorkId:
        assert isinstance(self.work.pk, (int, UUID)), "work rows use integer or UUID primary keys"
        return self.work.pk

    @property
    def model(self) -> type[ClaimedWorkT]:
        return type(self.work)

    def owned_work(self) -> QuerySet[ClaimedWorkT]:
        """Select this captured claim's authority; callers own locking and lease checks."""
        return self.model.objects.filter(
            pk=self.work_id,
            state=DurableWorkState.CLAIMED,
            fencing_token=self.token,
            desired_revision=self.desired_revision,
        )


class WorkOwnershipLost(RuntimeError):
    """Raised before provider I/O when this worker no longer owns the claim."""


@dataclass(slots=True)
class _SettlementScope:
    work: DurableWork
    active: bool = True


_settlement: ContextVar[_SettlementScope | None] = ContextVar("durable_work_settlement", default=None)


def require_fenced_settlement(work: DurableWork) -> None:
    """Canonical product writers may run only in this work's current settlement."""
    scope = _settlement.get()
    if scope is None or not scope.active or scope.work is not work or not connection.in_atomic_block:
        raise RuntimeError("product publication requires this work's fenced settlement")


def finalize_under_fence[WorkT: DurableWork, ResultT](work: WorkT, finalize: Callable[[WorkT], ResultT]) -> ResultT:
    """Run local publication under caller-held authority, within its transaction."""
    if not connection.in_atomic_block:
        raise RuntimeError("fenced settlement requires a transaction and caller-held locks")
    scope = _SettlementScope(work)
    token = _settlement.set(scope)
    try:
        return finalize(work)
    finally:
        scope.active = False  # A copied context cannot keep the permission after return.
        _settlement.reset(token)


@dataclass(frozen=True, slots=True)
class WorkExecutionContext[ClaimedWorkT: DurableWork]:
    """Lease-aware operations available while executing one immutable claim."""

    claim: WorkClaim[ClaimedWorkT]
    lease_duration: timedelta

    @contextmanager
    def external_call(self) -> Iterator[None]:
        """
        Check and renew before ordinary provider calls inside this block.

        This is an entry guard, not a lock held at the provider. Worker hard
        limits must remain shorter than the lease, including legacy routes.
        """

        require_autocommit()
        if not renew_work(self.claim, lease_duration=self.lease_duration):
            raise WorkOwnershipLost(
                f"lost {self.claim.model._meta.label_lower} claim {self.claim.work_id} before provider I/O"
            )
        yield


def require_autocommit() -> None:
    """A nested atomic block does not commit a claim before external I/O."""
    if connection.in_atomic_block or not connection.get_autocommit():
        raise RuntimeError("durable-work dispatch/execution requires autocommit")


def database_now() -> datetime:
    """Read the coordination clock from PostgreSQL, the lease authority."""
    return _read_database_clock()


def _read_database_clock() -> datetime:
    """Clock transport seam owned by the public testing control."""
    with connection.cursor() as cursor:
        # Static coordination query; no request or domain data reaches SQL.
        # nosemgrep: python.django.security.audit.raw-query.raw-query
        cursor.execute("SELECT clock_timestamp()")
        row = cursor.fetchone()
    assert row is not None, "PostgreSQL must return its current timestamp"
    value = row[0]
    assert isinstance(value, datetime), "PostgreSQL timestamp must decode to datetime"
    return value


def claim_work[WorkT: DurableWork](
    model: type[WorkT],
    work_id: WorkId,
    *,
    lease_duration: timedelta,
    now: NowFn = database_now,
    queued: bool = False,
    expected_revision: int | None = None,
    eligibility: ExecutionEligibility | None = None,
) -> WorkClaim[WorkT] | None:
    """
    Claim one due row and return the exact in-memory fenced instance.

    INVARIANT: the row lock and state re-read are the claim arbitration point.
    Returning this instance, rather than refetching after commit, prevents a
    stalled worker from adopting a successor's fencing token. Synchronous
    callers' expected revision is checked here before any claim mutation.
    """

    with transaction.atomic():
        claimed_at = now()
        candidates = model.objects.select_for_update(of=("self",)).filter(
            id=work_id, state__in=CLAIMABLE_STATES, available_at__lte=claimed_at
        )
        if eligibility is not None:
            candidates = eligibility.filter(candidates, now=claimed_at)
        work = candidates.first()
        if work is None or (expected_revision is not None and work.desired_revision != expected_revision):
            record_work_operation(
                model._meta.label_lower,
                DurableWorkOperation.CLAIM_MISSED,
            )
            return None
        work.state = DurableWorkState.QUEUED if queued else DurableWorkState.CLAIMED
        work.fencing_token = uuid4()
        # Both counters saturate: neither fences anything (the token does).
        work.claim_generation = saturating_increment(work.claim_generation)
        work.attempt_count = saturating_increment(work.attempt_count)
        # The lease runs from the instant the row was judged due, read before
        # its lock: any lock wait (a short transaction) comes off the lease.
        work.lease_expires_at = claimed_at + lease_duration
        work.last_error_code = ""
        work.save(
            update_fields=[
                "state",
                "fencing_token",
                "claim_generation",
                "attempt_count",
                "lease_expires_at",
                "last_error_code",
                "updated_at",
            ]
        )
    record_work_operation(
        model._meta.label_lower,
        DurableWorkOperation.CLAIMED,
    )
    return WorkClaim(
        work=work,
        token=work.fencing_token,
        claim_generation=work.claim_generation,
        desired_revision=work.desired_revision,
    )


def renew_work[WorkT: DurableWork](
    claim: WorkClaim[WorkT],
    *,
    lease_duration: timedelta,
    now: NowFn = database_now,
) -> bool:
    renewed_at = now()
    updated = claim.owned_work().update(
        lease_expires_at=renewed_at + lease_duration,
        updated_at=renewed_at,
    )
    renewed = updated == 1
    record_work_operation(
        claim.model._meta.label_lower,
        (DurableWorkOperation.RENEWED if renewed else DurableWorkOperation.OWNERSHIP_LOST),
    )
    return renewed


def reopen_locked_work[WorkT: DurableWork](current: WorkT, *, available_at: datetime, reason: str) -> None:
    """
    Repair the same settled revision; caller holds its row lock in a transaction.

    This never grants a fresh retry budget or revives exhausted work. No product
    callback runs here, so owner locking belongs to the calling settlement.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("reopening locked work requires a transaction")
    if current.state != DurableWorkState.SUCCEEDED:
        raise WorkOwnershipLost("only settled work may be reopened for same-revision repair")
    current.state = DurableWorkState.READY
    current.available_at = available_at
    current.completed_at = None
    current.lease_expires_at = None
    current.last_error_code = reason[:64]
    current.save(
        update_fields=["state", "available_at", "completed_at", "lease_expires_at", "last_error_code", "updated_at"]
    )
    record_work_operation(current._meta.label_lower, DurableWorkOperation.CONTINUED)


def settle_locked_work[WorkT: DurableWork](
    current: WorkT,
    *,
    revision: int,
    finalize: Callable[[WorkT], ReopenAfter | None] | None = None,
    now: NowFn = database_now,
) -> None:
    """
    Settle admitted evidence under caller-held locks, then publish local state.

    The caller must hold the work lock and establish authority for this exact
    revision. Callers with a product finalizer also lock the owner first. This
    helper does not infer permission from a receipt, cancellation or claim.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("settling locked work requires a transaction")
    if current.desired_revision != revision:
        raise WorkOwnershipLost("settlement revision was superseded")
    completed_at = now()
    current.state = DurableWorkState.SUCCEEDED
    current.applied_revision = revision
    current.lease_expires_at = None
    current.completed_at = completed_at
    current.last_error_code = ""
    current.save(
        update_fields=["state", "applied_revision", "lease_expires_at", "completed_at", "last_error_code", "updated_at"]
    )
    followup = finalize_under_fence(current, finalize) if finalize is not None else None
    pin_work_beneficiary(current)
    if followup is not None:
        reopen_locked_work(current, available_at=completed_at + followup.delay, reason=followup.reason)
    else:
        record_work_operation(current._meta.label_lower, DurableWorkOperation.SUCCEEDED)


def complete_work[WorkT: DurableWork](
    claim: WorkClaim[WorkT],
    *,
    finalize: Callable[[WorkT], ReopenAfter | None] | None = None,
    now: NowFn = database_now,
) -> bool:
    """
    Accept a fenced settlement, optionally scheduling a same-revision repair.

    True means the claim's settlement was accepted, not that no future repair
    is owed. Stale claims cannot run the finalizer or change the current row.
    """
    with transaction.atomic():
        lock_work_owner(claim.work)
        current = claim.owned_work().select_for_update().first()
        if current is not None:
            settle_locked_work(current, revision=claim.desired_revision, finalize=finalize, now=now)
    if current is None:
        record_work_operation(claim.model._meta.label_lower, DurableWorkOperation.OWNERSHIP_LOST)
    return current is not None


def retry_locked_work[WorkT: DurableWork](
    current: WorkT,
    *,
    retry_at: datetime,
    max_rearms: int | None,
    retry_delay: timedelta,
    error_code: str,
    finalize: Callable[[WorkT], None] | None = None,
) -> None:
    """Shared retry/exhaustion transition; caller already owns this work row."""
    if max_rearms is not None and current.rearm_count >= max_rearms:
        current.state = DurableWorkState.EXHAUSTED
        current.completed_at = retry_at
    else:
        current.state = DurableWorkState.RETRYABLE
        # With no budget nothing retires the row first: a corrupt maximum must not fail every retry.
        current.rearm_count = saturating_increment(current.rearm_count)
        current.available_at = retry_at + retry_delay
    current.lease_expires_at = None
    current.last_error_code = error_code[:64]
    current.save(
        update_fields=[
            "state",
            "rearm_count",
            "available_at",
            "lease_expires_at",
            "last_error_code",
            "completed_at",
            "updated_at",
        ]
    )
    if finalize is not None:
        finalize_under_fence(current, finalize)


def retry_work[WorkT: DurableWork](
    claim: WorkClaim[WorkT],
    *,
    error_code: str,
    max_rearms: int | None,
    retry_delay: timedelta,
    finalize: Callable[[WorkT], None] | None = None,
    now: NowFn = database_now,
) -> bool:
    """Fenced retry/exhaustion transition for a definite replay-safe failure."""

    with transaction.atomic():
        if finalize is not None:
            lock_work_owner(claim.work)
        retry_at = now()
        current = claim.owned_work().select_for_update().first()
        if current is None:
            record_work_operation(
                claim.model._meta.label_lower,
                DurableWorkOperation.OWNERSHIP_LOST,
            )
            return False
        if current.requires_effect_evidence and current.has_possible_effect():
            raise ValueError("retry cannot bypass a possible provider attempt")
        retry_locked_work(
            current,
            retry_at=retry_at,
            max_rearms=max_rearms,
            retry_delay=retry_delay,
            error_code=error_code,
            finalize=finalize,
        )
    record_work_operation(
        claim.model._meta.label_lower,
        (
            DurableWorkOperation.EXHAUSTED
            if current.state == DurableWorkState.EXHAUSTED
            else DurableWorkOperation.RETRYABLE
        ),
    )
    return True


def terminal_locked_work[WorkT: DurableWork](
    current: WorkT,
    *,
    failed_at: datetime,
    error_code: str,
    finalize: Callable[[WorkT], None] | None = None,
) -> None:
    """Shared terminal transition; caller holds current owner/work authority."""
    current.state = DurableWorkState.TERMINAL_FAILED
    current.lease_expires_at = None
    current.completed_at = failed_at
    current.last_error_code = error_code[:64]
    current.save(update_fields=["state", "lease_expires_at", "completed_at", "last_error_code", "updated_at"])
    if finalize is not None:
        finalize_under_fence(current, finalize)


def terminal_work[WorkT: DurableWork](
    claim: WorkClaim[WorkT],
    *,
    error_code: str,
    finalize: Callable[[WorkT], None] | None = None,
    now: NowFn = database_now,
) -> bool:
    """Record a definite permanent failure under the exact claim fence."""

    failed_at = now()
    with transaction.atomic():
        lock_work_owner(claim.work)
        current = claim.owned_work().select_for_update().first()
        if current is not None:
            terminal_locked_work(current, failed_at=failed_at, error_code=error_code, finalize=finalize)
            pin_work_beneficiary(current)
    terminal = current is not None
    record_work_operation(
        claim.model._meta.label_lower,
        (DurableWorkOperation.TERMINAL_FAILED if terminal else DurableWorkOperation.OWNERSHIP_LOST),
    )
    return terminal


def unknown_locked_work[WorkT: DurableWork](
    current: WorkT,
    *,
    error_code: str,
    finalize: Callable[[WorkT], None] | None = None,
) -> None:
    """Retain uncertain debt under caller-held owner/work locks."""
    if not connection.in_atomic_block:
        raise RuntimeError("unknown settlement requires a transaction")
    current.state = DurableWorkState.UNKNOWN
    current.lease_expires_at = None
    current.completed_at = None
    current.last_error_code = error_code[:64]
    current.save(update_fields=["state", "lease_expires_at", "completed_at", "last_error_code", "updated_at"])
    if finalize is not None:
        finalize_under_fence(current, finalize)
    pin_work_beneficiary(current)


def unknown_work[WorkT: DurableWork](
    claim: WorkClaim[WorkT],
    *,
    error_code: str,
    finalize: Callable[[WorkT], None] | None = None,
) -> bool:
    """Release local ownership while retaining unresolved, non-dispatchable debt."""
    with transaction.atomic():
        lock_work_owner(claim.work)
        current = claim.owned_work().select_for_update().first()
        updated = current is not None
        if current is not None:
            unknown_locked_work(current, error_code=error_code, finalize=finalize)
    record_work_operation(
        claim.model._meta.label_lower,
        DurableWorkOperation.UNKNOWN if updated else DurableWorkOperation.OWNERSHIP_LOST,
    )
    return bool(updated)


def _owner_relation(model: type[DurableWork]) -> models.OneToOneField[Any, Any] | None:
    if model.work_owner_field is None:
        return None
    field = model._meta.get_field(model.work_owner_field)
    if not isinstance(field, models.OneToOneField):
        raise TypeError("work_owner_field must name a OneToOneField")
    return field


def lock_work_owner(work: DurableWork) -> models.Model | None:
    """Use the same owner-before-work order as requirement creation."""
    field = _owner_relation(type(work))
    if field is not None:
        # Immutable work outlives owner deletion (SET_NULL). The original
        # snapshot may still carry the deleted id; absence needs no owner lock.
        owner_manager = getattr(field.remote_field.model, work.work_owner_manager_name)
        return owner_manager.select_for_update().filter(pk=getattr(work, field.attname)).first()
    return None


def lock_owner_of_work(model: type[DurableWork], work_id: WorkId) -> None:
    """
    Lock the owner a stored work row names, before the caller locks that row: one statement.

    The owner-before-work order of ``lock_work_owner`` without first reading
    the row to learn its owner. The sub-select that names the owner takes no
    lock, so the work row is still locked only after its owner. An owner
    deleted earlier (SET_NULL) or a vanished row locks nothing; the caller's
    locked read of the work row decides whether there is anything to do.
    """
    field = _owner_relation(model)
    if field is not None:
        owner_ids = model.objects.filter(pk=work_id).values(field.attname)
        owner_manager = getattr(field.remote_field.model, model.work_owner_manager_name)
        owner_manager.select_for_update().filter(pk__in=owner_ids).first()


def rearm_stopped_work[WorkT: DurableWork](current: WorkT, *, now: NowFn = database_now) -> None:
    """Retry identical input under its row lock, retaining revision and effect evidence."""
    if not connection.in_atomic_block:
        raise RuntimeError("rearming stopped work requires its admission transaction")
    if current.requires_effect_evidence:
        raise ValueError("provider occurrences require evidence reconciliation")
    _rearm_stopped_work(current, now=now)


def _rearm_stopped_work[WorkT: DurableWork](current: WorkT, *, now: NowFn) -> None:
    """Shared mutation after the calling policy has proved rearm permission."""
    if current.state not in (DurableWorkState.EXHAUSTED, DurableWorkState.TERMINAL_FAILED):
        raise WorkOwnershipLost("only stopped failures can receive a fresh retry budget")
    current.state = DurableWorkState.READY
    # The input is unchanged, so old UNKNOWN writes cannot overwrite a different
    # revision yet. Keep their evidence for future input changes; fence old claims
    # with a new token instead of making that uncertainty spuriously stale.
    current.fencing_token = uuid4()
    current.rearm_count = 0
    current.lease_expires_at = None
    current.available_at = now()
    current.completed_at = None
    current.last_error_code = ""
    current.save(
        update_fields=[
            "state",
            "fencing_token",
            "rearm_count",
            "lease_expires_at",
            "available_at",
            "completed_at",
            "last_error_code",
            "updated_at",
        ]
    )
    record_work_operation(current._meta.label_lower, DurableWorkOperation.CONTINUED)


def resume_unapplied_work[WorkT: DurableWork](
    model: type[WorkT], work_id: WorkId, *, reset_budget: bool = False
) -> bool:
    """Resume a paused or exhausted occurrence only with no unresolved effect.

    An operator may refresh the failure budget, never erase evidence or replace
    the occurrence's revision. Active owners, UNKNOWN and success stay intact.
    """
    if not model.requires_effect_evidence:
        raise ValueError("use wake_work for replay-safe work")
    with transaction.atomic():
        lock_owner_of_work(model, work_id)
        current = model.objects.select_for_update().filter(pk=work_id).first()
        if current is None:
            return False
        if current.state not in CLAIMABLE_STATES and not (reset_budget and current.state == DurableWorkState.EXHAUSTED):
            return False
        # INVARIANT: the authoritative evidence guard runs under the same row
        # lock as replay permission. Partial batch success remains in progress;
        # any uncertain crossing still forbids operator re-entry.
        if current.has_possible_effect():
            return False
        if current.state == DurableWorkState.EXHAUSTED:
            _rearm_stopped_work(current, now=database_now)
        else:
            # Resume can shorten a pause, never postpone already-due work.
            resumed_at = database_now()
            if current.available_at > resumed_at:
                current.available_at = resumed_at
                current.save(update_fields=["available_at", "updated_at"])
        return True


def supersede_work[WorkT: DurableWork](
    model: type[WorkT],
    work_id: WorkId,
    *,
    now: NowFn = database_now,
    reset_budget: bool = True,
) -> bool:
    """
    Make a newer desired revision claimable and invalidate old claim snapshots.

    ``reset_budget=False`` keeps ``rearm_count``: a fenced pass was interrupted
    by new facts, not failed, so it earns no fresh retry budget.
    """

    if model.requires_effect_evidence:
        raise ValueError("provider operations preserve their occurrence; evidence cannot be bypassed by supersession")
    superseded_at = now()
    updated = model.objects.filter(id=work_id).update(
        state=DurableWorkState.READY,
        desired_revision=F("desired_revision") + 1,
        **({"rearm_count": 0} if reset_budget else {}),
        lease_expires_at=None,
        available_at=superseded_at,
        completed_at=None,
        last_error_code="",
        updated_at=superseded_at,
    )
    superseded = updated == 1
    if superseded:
        record_work_operation(
            model._meta.label_lower,
            DurableWorkOperation.SUPERSEDED,
        )
    return superseded


class WakeAction(StrEnum):
    """What waking a dependent did to its row; every value but QUEUED owes a notification."""

    DUE = "due"
    """READY/RETRYABLE: due now, revision and backoff budget kept."""
    QUEUED = "queued"
    """A reserved delivery claims after this commit and reads the new facts."""
    FENCED = "fenced"
    """CLAIMED: a pass that may have read the old facts gets a new revision; budget kept."""
    REOPENED = "reopened"
    """SUCCEEDED: nothing runs to fence, so the same revision reopens; budget kept."""
    REARMED = "rearmed"
    """EXHAUSTED/TERMINAL_FAILED: new facts earn a new revision and a fresh budget."""


def wake_work[WorkT: DurableWork](model: type[WorkT], work_id: WorkId, *, now: NowFn = database_now) -> WakeAction:
    """
    Make an existing dependent read facts committing in this transaction.

    Locks only the dependent's work row, never its owner: a deliberate
    exception to owner-before-work. It writes coordination columns alone, and
    claims lock the same row without the owner, so a claim either finishes
    before this wake (and is fenced) or waits for its commit (and reads the new
    facts). Callers take every upstream lock first; the dependent row is the
    transaction's last lock, so no dependent path may wait on an upstream row.
    """
    if model.requires_effect_evidence:
        raise ValueError("an occurrence cannot be superseded; wake only replay-safe dependents")
    with transaction.atomic():
        work = model.objects.select_for_update(of=("self",)).get(pk=work_id)
        woken_at = now()
        if work.state == DurableWorkState.QUEUED:
            return WakeAction.QUEUED
        if work.state in CLAIMABLE_STATES:
            # Backoff waited for new facts; they have arrived. Revision and budget stand.
            model.objects.filter(pk=work.pk, available_at__gt=woken_at).update(
                available_at=woken_at, updated_at=woken_at
            )
            return WakeAction.DUE
        if work.state == DurableWorkState.CLAIMED:
            assert supersede_work(model, work.pk, now=now, reset_budget=False)
            return WakeAction.FENCED
        if work.state == DurableWorkState.SUCCEEDED:
            # Progress fields and checkpoints keyed to this revision stay valid.
            reopen_locked_work(work, available_at=woken_at, reason="woken")
            return WakeAction.REOPENED
        if work.state in TERMINAL_WORK_STATES:
            assert supersede_work(model, work.pk, now=now)
            return WakeAction.REARMED
        raise ValueError(f"cannot wake {model._meta.label_lower}:{work.pk} in state {work.state}")


def reap_expired_work[WorkT: DurableWork](
    model: type[WorkT],
    *,
    max_rearms: int | None,
    retry_delay: timedelta,
    batch_size: int = 500,
    now: NowFn = database_now,
    finalize_unknown: Callable[[WorkT], None] | None = None,
    finalize_retry: Callable[[WorkT, Exception], None] | None = None,
    retry_delay_for: Callable[[WorkT], timedelta] | None = None,
    settle_expired_claim: Callable[[WorkT], bool] | None = None,
    recovery: RecoveryPass | None = None,
) -> int:
    """
    Recover expired claims without holding locks across the batch.

    An expired QUEUED reservation becomes due again with its budget intact; an
    expired CLAIMED execution is a failed attempt and is retried or exhausted.
    Each claim is reaped in its own transaction and isolated: a finalizer that
    raises rolls back only that claim, which stays expired and owed.
    """

    reaped_at = now()
    with recovery_pass_for(model._meta.label_lower, recovery) as tick:
        # A claim whose reap raised is held out with backoff: it stays expired
        # and owed, and no longer leads every batch ahead of healthy claims.
        reaped = tick.run_page(
            RecoveryPhase.REAP,
            model.objects.filter(
                state__in=[DurableWorkState.QUEUED, DurableWorkState.CLAIMED],
                lease_expires_at__lt=reaped_at,
            )
            .order_by("lease_expires_at", "id")
            .values_list("id", flat=True),
            batch_size,
            partial(
                _reap_one,
                model,
                reaped_at=reaped_at,
                max_rearms=max_rearms,
                retry_delay=retry_delay,
                finalize_unknown=finalize_unknown,
                finalize_retry=finalize_retry,
                retry_delay_for=retry_delay_for,
                settle_expired_claim=settle_expired_claim,
            ),
        )
        # Counted before the tick raises what it isolated.
        if reaped:
            record_work_operation(
                model._meta.label_lower,
                DurableWorkOperation.REAPED,
                count=reaped,
            )
    return reaped


def _reap_one[WorkT: DurableWork](
    model: type[WorkT],
    work_id: WorkId,
    *,
    reaped_at: datetime,
    max_rearms: int | None,
    retry_delay: timedelta,
    finalize_unknown: Callable[[WorkT], None] | None,
    finalize_retry: Callable[[WorkT, Exception], None] | None,
    retry_delay_for: Callable[[WorkT], timedelta] | None,
    settle_expired_claim: Callable[[WorkT], bool] | None,
) -> int:
    with transaction.atomic():
        if (
            model.requires_effect_evidence
            or finalize_retry is not None
            or finalize_unknown is not None
            or settle_expired_claim is not None
        ):
            # These callbacks may write the owner's product state: owner first.
            lock_owner_of_work(model, work_id)
        work = (
            model.objects.select_for_update()
            .filter(
                id=work_id,
                state__in=[DurableWorkState.QUEUED, DurableWorkState.CLAIMED],
                lease_expires_at__lt=reaped_at,
            )
            .first()
        )
        if work is None:
            return 0
        uncertain = work.requires_effect_evidence and work.has_possible_effect()
        if uncertain:
            unknown_locked_work(work, error_code="lease_expired_after_attempt", finalize=finalize_unknown)
        elif work.state == DurableWorkState.QUEUED:
            # Acceptance is an atomic QUEUED -> CLAIMED transition. An
            # expired reservation therefore proves this delivery never
            # entered execution; transport loss cannot spend its budget.
            work.state = DurableWorkState.RETRYABLE
            work.available_at = reaped_at
            work.lease_expires_at = None
            work.last_error_code = "delivery_expired"
            work.save(update_fields=["state", "available_at", "lease_expires_at", "last_error_code", "updated_at"])
        elif settle_expired_claim is not None and settle_expired_claim(work):
            # A durable rejection owns its retry/terminal decision. Generic
            # lease expiry must not erase it or spend its retry budget twice.
            pass
        else:
            retry_locked_work(
                work,
                retry_at=reaped_at,
                max_rearms=max_rearms,
                retry_delay=retry_delay_for(work) if retry_delay_for is not None else retry_delay,
                error_code="lease_expired",
                finalize=(
                    lambda current: finalize_retry(current, RuntimeError("Claim expired before a possible effect"))
                )
                if finalize_retry is not None
                else None,
            )
        if finalize_unknown is not None or finalize_retry is not None:
            pin_work_beneficiary(work)
        return 1


def dispatch_due_work[WorkT: DurableWork](
    model: type[WorkT],
    *,
    publish: Publisher,
    lease_duration: timedelta,
    batch_size: int = 500,
    now: NowFn = database_now,
    eligibility: ExecutionEligibility | None = None,
    recovery: RecoveryPass | None = None,
) -> int:
    """Reserve before publication; only that delivery can start execution."""

    require_autocommit()

    def reserve_and_publish(work_id: WorkId) -> bool:
        claim = claim_work(model, work_id, lease_duration=lease_duration, now=now, queued=True, eligibility=eligibility)
        if claim is None:
            return False
        # Ambiguous sends retain their reservation until the lease expires.
        publish_work(publish, work_id, claim.token)
        return True

    with recovery_pass_for(model._meta.label_lower, recovery) as tick:
        published = _dispatch_recovery_page(
            due_work(model, now=now, eligibility=eligibility),
            dispatch=reserve_and_publish,
            batch_size=batch_size,
            recovery=tick,
        )
        # Counted before the tick raises what it isolated.
        if published:
            record_work_operation(
                model._meta.label_lower,
                DurableWorkOperation.DISPATCHED,
                count=published,
            )
    return published


def outstanding_work[WorkT: DurableWork](model: type[WorkT]) -> QuerySet[WorkT]:
    """Canonical active obligations, including delayed retries and reservations."""
    return model.objects.filter(state__in=ACTIVE_STATES).order_by("id")


def due_work[WorkT: DurableWork](
    model: type[WorkT],
    *,
    now: NowFn = database_now,
    eligibility: ExecutionEligibility | None = None,
) -> QuerySet[WorkT]:
    """Return the unbounded production selection for conformance proofs."""

    due_at = now()
    candidates = (
        outstanding_work(model)
        .filter(
            Q(
                state=DurableWorkState.READY,
                available_at__lte=due_at,
            )
            | Q(
                state=DurableWorkState.RETRYABLE,
                available_at__lte=due_at,
            )
        )
        .order_by("available_at", "id")
    )

    return candidates if eligibility is None else eligibility.filter(candidates, now=due_at)


def publish_work_census[WorkT: DurableWork](
    model: type[WorkT],
    *,
    now: NowFn = database_now,
    undiscovered: int = 0,
    eligibility: ExecutionEligibility | None = None,
) -> None:
    """Count active and failed obligations without reading retained successes."""

    observed_at = now()
    rows = {
        DurableWorkState(row["state"]): (row["count"], row["oldest"])
        for row in model.objects.filter(state__in=ATTENTION_WORK_STATES)
        .values("state")
        .annotate(count=Count("id"), oldest=Min("created_at"))
    }
    record_outstanding(
        model._meta.label_lower, undiscovered + sum(rows.get(state, (0, None))[0] for state in ACTIVE_STATES)
    )
    for value in ATTENTION_WORK_STATES:
        state = DurableWorkState(value)
        count, oldest = rows.get(state, (0, None))
        oldest_age_seconds = 0.0 if oldest is None else max(0.0, (observed_at - oldest).total_seconds())
        record_work_census(
            model._meta.label_lower,
            state,
            count=count,
            oldest_age_seconds=oldest_age_seconds,
        )

    if eligibility is not None:
        # Waiting remains part of total outstanding. Expose why time-due rows
        # are quiet without per-row inspection or queries for ungated adopters.
        candidates = due_work(model, now=lambda: observed_at)
        # Count on this scan directly. An IN(eligible IDs) subquery reads due
        # work a second time on every tick, even when all of it stays parked.
        counts = candidates.aggregate(
            total=Count("pk"), eligible=Count("pk", filter=eligibility.predicate(now=observed_at))
        )
        record_execution_eligibility(
            model._meta.label_lower, eligible=counts["eligible"], waiting=counts["total"] - counts["eligible"]
        )


def prune_completed_work[WorkT: DurableWork](
    model: type[WorkT],
    *,
    completed_before: datetime,
    batch_size: int = 500,
) -> int:
    """Delete a bounded batch of terminal coordination rows after retention."""

    terminal_ids = list(
        model.objects.filter(
            state=DurableWorkState.SUCCEEDED,
            completed_at__lt=completed_before,
        )
        .order_by("completed_at", "id")
        .values_list("id", flat=True)[:batch_size]
    )
    if not terminal_ids:
        return 0
    # Re-check terminal eligibility in the DELETE itself: a succeeded
    # projection may be superseded between the candidate scan and this write.
    _deleted, details = model.objects.filter(
        id__in=terminal_ids,
        state=DurableWorkState.SUCCEEDED,
        completed_at__lt=completed_before,
    ).delete()
    pruned = details.get(model._meta.label, 0)
    if pruned:
        record_work_operation(
            model._meta.label_lower,
            DurableWorkOperation.PRUNED,
            count=pruned,
        )
    return pruned


def accept_delivery[WorkT: DurableWork](
    model: type[WorkT],
    work_id: WorkId,
    token: UUID,
    *,
    lease_duration: timedelta,
    expected_revision: int | None = None,
    eligibility: ExecutionEligibility | None = None,
) -> WorkClaim[WorkT] | None:
    """A duplicated broker message cannot start a second executor."""
    with transaction.atomic():
        work = (
            model.objects.select_for_update()
            .filter(
                pk=work_id,
                state=DurableWorkState.QUEUED,
                fencing_token=token,
            )
            .first()
        )
        if work is None or (expected_revision is not None and work.desired_revision != expected_revision):
            return None
        accepted_at = database_now()
        if (
            eligibility is not None
            and not eligibility.filter(model.objects.filter(pk=work.pk), now=accepted_at).exists()
        ):
            # A queued message is only a hint. Revoke this exact reservation if
            # its prerequisite disappeared; preserve intent, revision and budget.
            work.state = DurableWorkState.READY
            work.fencing_token = uuid4()
            work.lease_expires_at = None
            work.save(update_fields=["state", "fencing_token", "lease_expires_at", "updated_at"])
            record_work_operation(model._meta.label_lower, DurableWorkOperation.INELIGIBLE_DELIVERY)
            return None
        work.state = DurableWorkState.CLAIMED
        work.lease_expires_at = accepted_at + lease_duration
        work.save(update_fields=["state", "lease_expires_at", "updated_at"])
    return WorkClaim(work, token, work.claim_generation, work.desired_revision)


def run_work[WorkT: DurableWork, ResultT](
    model: type[WorkT],
    work_id: WorkId,
    *,
    handler: Callable[[WorkT, WorkExecutionContext[WorkT]], ResultT | ContinueAfter],
    classify_error: Callable[[Exception], WorkFailureDisposition],
    lease_duration: timedelta,
    max_rearms: int | None,
    retry_delay: timedelta,
    delivery_token: UUID | None = None,
    expected_revision: int | None = None,
    eligibility: ExecutionEligibility | None = None,
    finalize_success: Callable[[WorkT, ResultT], ReopenAfter | None] | None = None,
    finalize_terminal: Callable[[WorkT, Exception], None] | None = None,
    finalize_unknown: Callable[[WorkT], None] | None = None,
    finalize_retry: Callable[[WorkT, Exception], None] | None = None,
    retry_delay_for: Callable[[WorkT], timedelta] | None = None,
    provider_retry_after: Callable[[Exception], timedelta] | None = None,
    database_only: bool = False,
) -> bool:
    """Claim, run outside transactions in-domain, and fenced-finalize."""

    require_autocommit()
    claim = (
        claim_work(
            model, work_id, lease_duration=lease_duration, expected_revision=expected_revision, eligibility=eligibility
        )
        if delivery_token is None
        else accept_delivery(
            model,
            work_id,
            delivery_token,
            lease_duration=lease_duration,
            expected_revision=expected_revision,
            eligibility=eligibility,
        )
    )
    if claim is None:
        return False
    with current_universe_scope(claim.work.work_universe()), execution_external_services():
        return _execute_claim(
            claim,
            handler=handler,
            classify_error=classify_error,
            lease_duration=lease_duration,
            max_rearms=max_rearms,
            retry_delay=retry_delay,
            finalize_success=finalize_success,
            finalize_terminal=finalize_terminal,
            finalize_unknown=finalize_unknown,
            finalize_retry=finalize_retry,
            retry_delay_for=retry_delay_for,
            provider_retry_after=provider_retry_after,
            database_only=database_only,
        )


def _execute_claim[WorkT: DurableWork, ResultT](
    claim: WorkClaim[WorkT],
    *,
    handler: Callable[[WorkT, WorkExecutionContext[WorkT]], ResultT | ContinueAfter],
    classify_error: Callable[[Exception], WorkFailureDisposition],
    lease_duration: timedelta,
    max_rearms: int | None,
    retry_delay: timedelta,
    finalize_success: Callable[[WorkT, ResultT], ReopenAfter | None] | None,
    finalize_terminal: Callable[[WorkT, Exception], None] | None,
    finalize_unknown: Callable[[WorkT], None] | None,
    finalize_retry: Callable[[WorkT, Exception], None] | None,
    retry_delay_for: Callable[[WorkT], timedelta] | None,
    provider_retry_after: Callable[[Exception], timedelta] | None,
    database_only: bool,
) -> bool:
    model = claim.model
    context = WorkExecutionContext(claim=claim, lease_duration=lease_duration)
    try:
        # Database callbacks and their settlement share one commit. Acquire the
        # producer's owner-before-work locks and revalidate authority before any
        # application write; external handlers retain their autocommit boundary.
        with transaction.atomic() if database_only else nullcontext():
            current = claim.work
            if database_only:
                owner = lock_work_owner(current)
                current = claim.owned_work().select_for_update().first()
                if current is None:
                    return False
                field = _owner_relation(type(current))
                if field is not None:
                    setattr(current, field.name, owner)
            with current_universe_scope(current.work_universe()):
                result = (
                    finalize_under_fence(current, lambda work: handler(work, context))
                    if database_only
                    else handler(current, context)
                )
            if isinstance(result, ContinueAfter):
                completed = continue_work(
                    claim,
                    result,
                    finalize_expired=_bind_finalizer(finalize_retry, TimeoutError("continuation deadline reached")),
                )
            else:
                completed = complete_work(claim, finalize=_bind_finalizer(finalize_success, result))
            if database_only and not completed:
                raise WorkOwnershipLost("database transition changed its own settlement fence")
    except Exception as error:
        possible_attempt = model.requires_effect_evidence and claim.work.has_possible_effect()
        disposition = WorkFailureDisposition.UNKNOWN if possible_attempt else classify_error(error)
        if disposition is WorkFailureDisposition.UNKNOWN:
            transitioned = unknown_work(claim, error_code=type(error).__name__, finalize=finalize_unknown)
        elif disposition is WorkFailureDisposition.RETRYABLE:
            delay = retry_delay_for(claim.work) if retry_delay_for is not None else retry_delay
            transitioned = retry_work(
                claim,
                error_code=type(error).__name__,
                max_rearms=max_rearms,
                # The provider's wait is a floor: a retry inside it is refused again.
                retry_delay=max(delay, provider_retry_after(error)) if provider_retry_after is not None else delay,
                finalize=_bind_finalizer(finalize_retry, error),
            )
        else:
            transitioned = terminal_work(
                claim,
                error_code=type(error).__name__,
                finalize=_bind_finalizer(finalize_terminal, error),
            )
        if not transitioned:
            logger.warning(
                "Durable effect failure lost its fence",
                extra={
                    "effect": model._meta.label_lower,
                    "work_id": claim.work_id,
                    "claim_generation": claim.claim_generation,
                    "disposition": disposition,
                },
            )
        raise
    if not completed:
        logger.warning(
            "Durable effect completion lost its fence",
            extra={
                "effect": model._meta.label_lower,
                "work_id": claim.work_id,
                "claim_generation": claim.claim_generation,
            },
        )
    return completed


def continue_work[WorkT: DurableWork](
    claim: WorkClaim[WorkT],
    continuation: ContinueAfter,
    *,
    finalize_expired: Callable[[WorkT], None] | None = None,
) -> bool:
    """Release this claim for a later check, preserving revisions and failure history."""
    now = database_now()
    expired = continuation.until is not None and now >= continuation.until
    available_at = now + continuation.delay
    if continuation.until is not None:
        available_at = min(available_at, continuation.until)
    with transaction.atomic():
        if expired:
            lock_work_owner(claim.work)
        updated = claim.owned_work().update(
            state=DurableWorkState.EXHAUSTED if expired else DurableWorkState.READY,
            lease_expires_at=None,
            available_at=available_at,
            completed_at=now if expired else None,
            last_error_code="continuation_deadline" if expired else "",
            updated_at=now,
        )
        if updated and expired:
            current = claim.model.objects.get(pk=claim.work_id)
            if finalize_expired is not None:
                finalize_under_fence(current, finalize_expired)
            pin_work_beneficiary(current)
    record_work_operation(
        claim.model._meta.label_lower,
        (
            DurableWorkOperation.OWNERSHIP_LOST
            if not updated
            else DurableWorkOperation.EXHAUSTED
            if expired
            else DurableWorkOperation.CONTINUED
        ),
    )
    return bool(updated)


def pin_work_beneficiary(work: DurableWork) -> None:
    """Publish read-after-write affinity for the work's declared product beneficiary."""
    beneficiary = work.work_beneficiary_user_id()
    if beneficiary is not None:
        pin_after_write(beneficiary)


def _bind_finalizer[WorkT: DurableWork, ValueT, FinalizedT](
    callback: Callable[[WorkT, ValueT], FinalizedT] | None, value: ValueT
) -> Callable[[WorkT], FinalizedT] | None:
    """Capture this execution's outcome for the later, fenced work-row callback."""
    if callback is None:
        return None
    return lambda current: callback(current, value)
