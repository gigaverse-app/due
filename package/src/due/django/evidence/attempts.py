"""Durable external service-entry and outcome evidence.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, QuerySet

from due.django.models import (
    ACTIVE_WORK_STATES,
    DurableWork,
    DurableWorkState,
    EffectAttempt,
    EffectAttemptOutcome,
    RepairSettledBy,
)
from due.django.runtime.coordination import (
    WorkClaim,
    WorkExecutionContext,
    WorkOwnershipLost,
    database_now,
    lock_work_owner,
    require_autocommit,
)
from due.django.runtime.metrics import record_pending_effect_attempts
from due.errors import EffectNotAppliedError, EffectOutcomeUnknownError

logger = logging.getLogger(__name__)


def attempts_for(work: DurableWork) -> QuerySet[EffectAttempt]:
    """Library transition/test seam; application policy uses unsettled_work()."""
    return EffectAttempt.objects.filter(work_model=work._meta.label_lower, work_id=work.pk)


def initial_import_work[WorkT: DurableWork](model: type[WorkT], work: WorkT) -> WorkT:
    """Lock a newly admitted occurrence for a one-time legacy history import."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("import uncertainty inside the owner/work recording transaction")
    if not isinstance(work, model):
        raise ValueError("work belongs to another capability")
    lock_work_owner(work)
    current = model.objects.select_for_update().get(pk=work.pk)
    if current.state != DurableWorkState.READY or current.attempt_count or attempts_for(current).exists():
        raise ValueError("legacy uncertainty can only initialize an unexecuted occurrence")
    if current.desired_revision < 1:
        raise ValueError("legacy import requires an admitted revision")
    return current


def import_unknown_effect_attempt[WorkT: DurableWork](
    model: type[WorkT], work: WorkT, *, prior_revision: bool = False
) -> WorkT:
    """Initialize legacy uncertainty, never rewrite a primitive-owned history."""
    current = initial_import_work(model, work)
    EffectAttempt.objects.create(
        work_model=model._meta.label_lower,
        work_id=current.pk,
        claim_token=current.fencing_token,
        # Revision zero precedes the first admitted document: an unknown old
        # payload is NOT an identical write of that document. Existing repair
        # selection must keep considering it after the current write succeeds.
        desired_revision=current.desired_revision - 1 if prior_revision else current.desired_revision,
        started_at=database_now(),
    )
    return current


def unsettled_work[WorkT: DurableWork](work: QuerySet[WorkT], *, strict: bool = False) -> QuerySet[WorkT]:
    """
    Select work that can still execute or has unresolved provider repair debt.

    Preserve the caller's owner/resource/universe scope. Evidence from ALL
    revisions counts: an old projection may still overwrite current state.
    UNKNOWN is an observation, not a settlement verdict; success of a later
    attempt does not prove that an earlier timed-out request finished.

    This is a database observation, not a lock or remote cancellation guarantee.
    A reuse gate must hold its application identity lock against new producers.
    Terminal failures without pending evidence are excluded; applications may
    independently require successful completion for their own product policy.
    """
    unresolved = Q(repair_pending=True)
    if strict:
        unresolved |= Q(outcome=EffectAttemptOutcome.UNKNOWN)
    pending = EffectAttempt.objects.filter(unresolved, work_model=work.model._meta.label_lower, work_id=OuterRef("pk"))
    return work.filter(Q(state__in=ACTIVE_WORK_STATES) | Q(Exists(pending)))


def attempt_by_id(model: type[DurableWork], attempt_id: UUID) -> EffectAttempt:
    """
    Fetch one exact receipt identity, scoped to the capability's work model.

    Applications must also authorize the associated product owner before
    importing evidence. This lookup grants neither ownership nor replay.
    """
    return EffectAttempt.objects.get(work_model=model._meta.label_lower, pk=attempt_id)


@contextmanager
def applied_effect_call[WorkT: DurableWork](context: WorkExecutionContext[WorkT]) -> Iterator[EffectAttempt]:
    """
    Record entry before I/O whose normal return proves application.

    Only EffectNotAppliedError proves rejection; every other interruption leaves
    UNKNOWN. Operations requiring a validated result receipt must instead use
    DurableExternalOperation's atomic receipt storage.
    """
    with context.external_call():
        attempt = record_effect_attempt(context.claim)
        try:
            yield attempt
        except EffectNotAppliedError:
            observe_effect_outcome(attempt, EffectAttemptOutcome.NOT_APPLIED)
            raise
        observe_effect_outcome(attempt, EffectAttemptOutcome.APPLIED)


def record_effect_attempt[WorkT: DurableWork](
    claim: WorkClaim[WorkT],
    *,
    before_commit: Callable[[WorkT, EffectAttempt], None] | None = None,
) -> EffectAttempt:
    """Commit an UNKNOWN attempt under the current fence, before external I/O."""
    require_autocommit()
    with transaction.atomic():
        if before_commit is not None:
            lock_work_owner(claim.work)
        current = claim.owned_work().select_for_update().filter(lease_expires_at__gt=database_now()).first()
        if current is None:
            raise WorkOwnershipLost("provider attempt requires the captured current claim")
        if current.requires_effect_evidence and current.has_possible_effect():
            raise EffectOutcomeUnknownError("this occurrence already has a possible or applied provider attempt")
        attempt = EffectAttempt.objects.create(
            work_model=claim.model._meta.label_lower,
            work_id=claim.work_id,
            claim_token=claim.token,
            desired_revision=claim.desired_revision,
            started_at=database_now(),
        )
        if before_commit is not None:
            before_commit(current, attempt)
        return attempt


def observe_effect_outcome(attempt: EffectAttempt, outcome: EffectAttemptOutcome) -> None:
    """
    Accept a definite receipt for this exact attempt, even after fence loss.

    No product state is published here. Contradictory definite receipts fail
    closed; recording the same observation twice is harmless.
    """
    if outcome is EffectAttemptOutcome.UNKNOWN:
        raise ValueError("an observation must provide definite evidence")
    with transaction.atomic():
        current = EffectAttempt.objects.select_for_update().get(pk=attempt.pk)
        if current.outcome == outcome:
            return
        if current.outcome != EffectAttemptOutcome.UNKNOWN:
            raise ValueError("provider evidence contradicts a previous definite outcome")
        current.outcome = outcome
        current.observed_at = database_now()
        current.repair_pending = outcome is EffectAttemptOutcome.APPLIED
        current.repair_settled_by = RepairSettledBy.RECEIPT if not current.repair_pending else ""
        current.save(update_fields=["outcome", "observed_at", "repair_pending", "repair_settled_by"])


def resolve_confirmed_attempts(model: type[DurableWork], *, batch_size: int) -> int:
    """
    Settle late APPLIED receipts for an already-applied identical revision.

    Scheduled actions and projections need no further I/O for these receipts.
    UNKNOWN and older revisions remain unresolved; success of another attempt
    does not prove those requests finished. See the guide's provider evidence
    and composition section for the distinction from pruning.
    """
    identical = model.objects.filter(
        pk=OuterRef("work_id"),
        state=DurableWorkState.SUCCEEDED,
        desired_revision=OuterRef("desired_revision"),
        applied_revision=OuterRef("desired_revision"),
    )
    resolved = list(
        EffectAttempt.objects.filter(
            work_model=model._meta.label_lower,
            outcome=EffectAttemptOutcome.APPLIED,
            repair_pending=True,
        )
        .filter(Exists(identical))
        .order_by("observed_at", "pk")
        .values_list("pk", flat=True)[:batch_size]
    )
    EffectAttempt.objects.filter(pk__in=resolved).update(
        repair_pending=False, repair_settled_by=RepairSettledBy.RECEIPT
    )
    return len(resolved)


def prune_resolved_attempts(
    model: type[DurableWork], *, before: datetime, batch_size: int, keep_started_evidence: bool = False
) -> int:
    """
    Remove old resolved receipts; unsettled evidence always survives.

    Scheduled actions keep one admission witness per revision, because their
    cancellation/replacement contract must still answer ALREADY_STARTED after
    compaction. Covered UNKNOWN has no observed_at; a later definite APPLIED
    receipt supplies its conservative retention clock without inventing one.
    Provider operations must not call this: their receipts authorize settlement
    and their admission history prevents a duplicate irreversible occurrence.
    """
    later = EffectAttempt.objects.filter(
        work_model=model._meta.label_lower,
        work_id=OuterRef("work_id"),
        desired_revision=OuterRef("desired_revision"),
    ).filter(Q(started_at__gt=OuterRef("started_at")) | Q(started_at=OuterRef("started_at"), pk__gt=OuterRef("pk")))
    covered_before = later.filter(outcome=EffectAttemptOutcome.APPLIED, repair_pending=False, observed_at__lt=before)
    candidates = (
        EffectAttempt.objects.filter(work_model=model._meta.label_lower, repair_pending=False)
        # Policy-settled UNKNOWN stays as operator evidence, never a receipt.
        .exclude(repair_settled_by__in=(RepairSettledBy.POLICY_WINDOW, RepairSettledBy.TARGET_RETIRED))
        .filter(Q(observed_at__lt=before) | (Q(outcome=EffectAttemptOutcome.UNKNOWN) & Q(Exists(covered_before))))
    )
    if keep_started_evidence:
        candidates = candidates.filter(
            Q(outcome=EffectAttemptOutcome.NOT_APPLIED)
            | Q(Exists(later.exclude(outcome=EffectAttemptOutcome.NOT_APPLIED)))
        )
    page = list(candidates.order_by("observed_at", "pk").values_list("pk", flat=True)[:batch_size])
    count, _ = EffectAttempt.objects.filter(pk__in=page, repair_pending=False).delete()
    return count


def expire_repair_window(model: type[DurableWork], *, window: timedelta | None, batch_size: int) -> int:
    """Stop bounded repair by policy; preserve UNKNOWN as evidence, never as a receipt."""
    if window is None:
        return 0
    if model.requires_effect_evidence:
        raise ValueError("non-repeatable operation evidence must never expire")
    from due.django.runtime.metrics import record_repair_expired

    with transaction.atomic():
        page = list(
            EffectAttempt.objects.select_for_update(skip_locked=True)
            .filter(
                work_model=model._meta.label_lower,
                outcome=EffectAttemptOutcome.UNKNOWN,
                repair_pending=True,
                started_at__lt=database_now() - window,
            )
            .order_by("started_at", "pk")[:batch_size]
        )
        EffectAttempt.objects.filter(pk__in=[attempt.pk for attempt in page]).update(
            repair_pending=False, repair_settled_by=RepairSettledBy.POLICY_WINDOW
        )
    for attempt in page:
        record_repair_expired(model._meta.label_lower)
        logger.warning(
            "Durable repair window expired: effect=%s work_id=%s attempt_id=%s revision=%s",
            model._meta.label_lower,
            attempt.work_id,
            attempt.pk,
            attempt.desired_revision,
        )
    return len(page)


def retire_repair_for_gone_target(work: DurableWork) -> int:
    """
    Settle this work's older-revision repair debt: its provider target is gone.

    Only for repeatable projections, and only on definitive provider evidence that
    the captured target no longer exists. An escaped older write cannot change a
    target that is gone, so nothing remains to repair. UNKNOWN stays UNKNOWN as
    evidence; a later definite receipt restores ordinary receipt-based repair.
    """
    if type(work).requires_effect_evidence:
        raise ValueError("non-repeatable operation evidence must never be retired")
    from due.django.runtime.metrics import record_repair_retired

    label = type(work)._meta.label_lower
    retired = (
        attempts_for(work)
        .filter(repair_pending=True, desired_revision__lt=work.desired_revision)
        .update(repair_pending=False, repair_settled_by=RepairSettledBy.TARGET_RETIRED)
    )
    if retired:
        record_repair_retired(label, retired)
        logger.warning(
            "Durable repair retired, provider target gone: effect=%s work_id=%s attempts=%s revision=%s",
            label,
            work.pk,
            retired,
            work.desired_revision,
        )
    return retired


def unknown_effect_attempts(model: type[DurableWork]) -> QuerySet[EffectAttempt]:
    """Operator inspection, including policy-expired evidence; never permission to replay."""
    return EffectAttempt.objects.filter(work_model=model._meta.label_lower, outcome=EffectAttemptOutcome.UNKNOWN)


def publish_pending_attempt_census(model: type[DurableWork]) -> None:
    """Count this capability's unsettled evidence, excluding retained history."""
    counts = {
        row["outcome"]: row["count"]
        for row in EffectAttempt.objects.filter(work_model=model._meta.label_lower, repair_pending=True)
        .values("outcome")
        .annotate(count=Count("pk"))
    }
    for outcome in EffectAttemptOutcome:
        record_pending_effect_attempts(model._meta.label_lower, outcome, counts.get(outcome, 0))
