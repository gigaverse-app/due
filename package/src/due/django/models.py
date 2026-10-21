"""Persisted work states, external service-attempt evidence and schema contracts.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

import uuid
from datetime import UTC
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import F
from django.db.models.functions import Now


class DurableWorkState(models.TextChoices):
    READY = "ready", "ready"
    QUEUED = "queued", "queued"
    CLAIMED = "claimed", "claimed"
    RETRYABLE = "retryable", "retryable"
    UNKNOWN = "unknown", "outcome unknown"
    SUCCEEDED = "succeeded", "succeeded"
    TERMINAL_FAILED = "terminal_failed", "terminal_failed"
    EXHAUSTED = "exhausted", "exhausted"


class EffectAttemptOutcome(models.TextChoices):
    UNKNOWN = "unknown", "outcome unknown"
    APPLIED = "applied", "definitely applied"
    NOT_APPLIED = "not_applied", "definitely not applied"


class RepairSettledBy(models.TextChoices):
    RECEIPT = "receipt", "definite receipt"
    POLICY_WINDOW = "policy_window", "repair window expired"
    TARGET_RETIRED = "target_retired", "provider target permanently gone"


class EffectAttempt(models.Model):
    """
    Immutable crossing identity; evidence survives claim and owner deletion.

    The model label and integer work identity deliberately are not a cascading
    relation: deleting a work row cannot destroy evidence about escaped I/O.
    Provider receipts remain in domain-owned models, linked by this UUID.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_model = models.CharField(max_length=128)
    work_id = models.BigIntegerField()
    claim_token = models.UUIDField()
    desired_revision = models.PositiveIntegerField()
    outcome = models.CharField(
        max_length=16, choices=EffectAttemptOutcome.choices, default=EffectAttemptOutcome.UNKNOWN
    )
    # Canonical application rejection serialized by its declared schema; old
    # evidence has NULL and retains its historical generic retry behavior.
    rejection = models.JSONField(null=True)
    started_at = models.DateTimeField(db_default=Now())
    observed_at = models.DateTimeField(null=True)
    repair_pending = models.BooleanField(default=True)
    repair_settled_by = models.CharField(max_length=20, choices=RepairSettledBy.choices, blank=True)

    class Meta:
        db_table = getattr(settings, "DUE_EFFECT_ATTEMPT_TABLE", "due_effectattempt")
        indexes = [
            models.Index(fields=["work_model", "work_id", "started_at"], name="effect_attempt_work_ix"),
            models.Index(
                fields=["work_model", "work_id"],
                condition=models.Q(repair_pending=True),
                name="effect_attempt_repair_ix",
            ),
            models.Index(
                fields=["work_model", "observed_at", "id"],
                condition=models.Q(repair_pending=False),
                name="effect_attempt_prune_ix",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(outcome=EffectAttemptOutcome.UNKNOWN, observed_at__isnull=True)
                    | models.Q(
                        outcome__in=[EffectAttemptOutcome.APPLIED, EffectAttemptOutcome.NOT_APPLIED],
                        observed_at__isnull=False,
                    )
                ),
                name="effect_attempt_evidence_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(rejection__isnull=True) | models.Q(outcome=EffectAttemptOutcome.NOT_APPLIED),
                name="effect_attempt_rejection_ck",
            ),
        ]


class RecoveryDeferral(models.Model):
    """
    An identity a recovery phase isolated, held out of that phase until ``retry_at``.

    Advisory scheduling, never obligation state: the identity stays owed in its
    own table (an expired claim, a discovery candidate, a receipt pending
    repair) and is retried with backoff, so one identity whose step always
    raises stops leading every tick and a fully poisoned page cannot starve the
    identities behind it. Deleting a row only makes its identity eligible sooner.
    See docs/architecture.md.
    """

    effect = models.CharField(max_length=128)
    phase = models.CharField(max_length=32)
    # The isolated identity's primary key as text: owners may be integer or UUID keyed.
    identity = models.CharField(max_length=64)
    failures = models.PositiveIntegerField(default=1)
    first_failed_at = models.DateTimeField(db_default=Now())
    last_failed_at = models.DateTimeField(db_default=Now())
    retry_at = models.DateTimeField()
    last_error = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = getattr(settings, "DUE_RECOVERY_DEFERRAL_TABLE", "due_recoverydeferral")
        constraints = [
            models.UniqueConstraint(fields=["effect", "phase", "identity"], name="recovery_deferral_identity_uq"),
        ]
        indexes = [
            models.Index(fields=["effect", "retry_at"], name="recovery_deferral_retry_ix"),
        ]


ACTIVE_WORK_STATES = (
    DurableWorkState.READY.value,
    DurableWorkState.QUEUED.value,
    DurableWorkState.CLAIMED.value,
    DurableWorkState.RETRYABLE.value,
    DurableWorkState.UNKNOWN.value,
)
TERMINAL_WORK_STATES = (
    DurableWorkState.SUCCEEDED.value,
    DurableWorkState.TERMINAL_FAILED.value,
    DurableWorkState.EXHAUSTED.value,
)
ATTENTION_WORK_STATES = (
    *ACTIVE_WORK_STATES,
    DurableWorkState.TERMINAL_FAILED.value,
    DurableWorkState.EXHAUSTED.value,
)


def durable_work_indexes(*, name_prefix: str) -> list[models.Index]:
    """Index due work, expired leases, attention census and bounded retention."""

    return [
        models.Index(
            fields=["state", "available_at", "id"],
            name=f"{name_prefix}_due_ix",
            condition=models.Q(
                state__in=[
                    DurableWorkState.READY.value,
                    DurableWorkState.RETRYABLE.value,
                ]
            ),
        ),
        models.Index(
            fields=["lease_expires_at"],
            name=f"{name_prefix}_lease_ix",
            condition=models.Q(state__in=[DurableWorkState.QUEUED.value, DurableWorkState.CLAIMED.value]),
        ),
        models.Index(
            fields=["state", "created_at"],
            include=["id"],
            name=f"{name_prefix}_census_ix",
            condition=models.Q(state__in=ATTENTION_WORK_STATES),
        ),
        models.Index(
            fields=["completed_at", "id"],
            name=f"{name_prefix}_prune_ix",
            condition=models.Q(state=DurableWorkState.SUCCEEDED.value),
        ),
    ]


def durable_work_constraints(*, name_prefix: str) -> list[models.BaseConstraint]:
    """Build the storage invariants required of every work table."""

    return [
        models.CheckConstraint(
            condition=(
                models.Q(
                    state__in=[DurableWorkState.QUEUED.value, DurableWorkState.CLAIMED.value],
                    lease_expires_at__isnull=False,
                )
                | ~models.Q(state__in=[DurableWorkState.QUEUED.value, DurableWorkState.CLAIMED.value])
                & models.Q(lease_expires_at__isnull=True)
            ),
            name=f"{name_prefix}_lease_ck",
        ),
        models.CheckConstraint(
            condition=(
                models.Q(
                    state__in=ACTIVE_WORK_STATES,
                    completed_at__isnull=True,
                )
                | models.Q(
                    state__in=TERMINAL_WORK_STATES,
                    completed_at__isnull=False,
                )
            ),
            name=f"{name_prefix}_completed_ck",
        ),
        models.CheckConstraint(
            condition=(models.Q(applied_revision__isnull=True) | models.Q(applied_revision__lte=F("desired_revision"))),
            name=f"{name_prefix}_revision_ck",
        ),
        models.CheckConstraint(
            condition=(
                ~models.Q(state=DurableWorkState.SUCCEEDED.value)
                | models.Q(
                    applied_revision__isnull=False,
                    applied_revision=F("desired_revision"),
                )
            ),
            name=f"{name_prefix}_success_ck",
        ),
    ]


def durable_work_schema(
    name_prefix: str,
) -> tuple[list[models.Index], list[models.BaseConstraint]]:
    """
    Return the complete database contract for one concrete effect table.

    Keeping this as one declaration prevents an adopter from accidentally
    adding the recovery indexes without the state constraints, or vice versa.
    The prefix remains explicit because it is part of Django's migration state.
    """

    return (
        durable_work_indexes(name_prefix=name_prefix),
        durable_work_constraints(name_prefix=name_prefix),
    )


class DurableWork(models.Model):
    """
    Coordination fields for a typed, domain-owned durable obligation.

    The domain owns identity, payload, provider semantics, and lifecycle entry.
    Execution mode decides whether expired ownership permits another provider
    call. Non-repeatable operations retain UNKNOWN until evidence resolves it.
    """

    work_owner_field: ClassVar[str | None] = None
    # Admission and settlement must use the same owner visibility and lock order.
    work_owner_manager_name: ClassVar[str] = "objects"
    requires_effect_evidence: ClassVar[bool] = False

    state = models.CharField(
        max_length=24,
        choices=DurableWorkState.choices,
        default=DurableWorkState.READY,
    )
    fencing_token = models.UUIDField(default=uuid.uuid4, editable=False)
    claim_generation = models.PositiveIntegerField(default=0)
    attempt_count = models.PositiveIntegerField(default=0)
    rearm_count = models.PositiveIntegerField(default=0)
    desired_revision = models.PositiveIntegerField(default=1)
    applied_revision = models.PositiveIntegerField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    available_at = models.DateTimeField(db_default=Now())
    last_error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def work_universe(self) -> str:
        raise NotImplementedError

    def has_possible_effect(self) -> bool:
        """
        An admitted operation may have applied or is known to have applied.

        This is admission evidence, not a request-liveness test. A completed
        request or settled repair can still have produced an irreversible effect;
        neither elapsed time nor repair_pending=False grants resend permission.
        """
        return (
            EffectAttempt.objects.filter(work_model=self._meta.label_lower, work_id=self.pk)
            .exclude(outcome=EffectAttemptOutcome.NOT_APPLIED)
            .exists()
        )

    def work_beneficiary_user_id(self) -> int | None:
        """Return the user whose next read must observe terminal settlement."""

        return None

    def provider_idempotency_key(self) -> str:
        """
        Name this row's requested revision to a provider that deduplicates by key.

        A primary key alone is unique only within one database. Provider key
        spaces outlive a database reset or restore and can be shared by
        another database (a local backend pointed at a shared workspace), and
        a reused key returns another obligation's earlier result as this
        one's. The immutable admission time makes the row's identity unique
        across those databases; the revision keeps a revised input from
        reusing the old input's key. Retries of this revision reuse it.
        """
        assert self.pk is not None and self.created_at is not None, "key a committed work row"
        admitted = self.created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return f"{self._meta.label_lower}:{self.pk}:{admitted}:r{self.desired_revision}"


class UniverseDurableWork(DurableWork):
    """
    Durable work carrying the tenant context restored during execution.

    Adopters inherit this base instead of implementing an ``work_universe``
    hook. They declare only their domain identity and immutable desired-state
    fields; the shared runner restores this persisted universe automatically.
    """

    universe = models.CharField(max_length=64, default="", blank=True)

    class Meta:
        abstract = True

    def work_universe(self) -> str:
        return self.universe
