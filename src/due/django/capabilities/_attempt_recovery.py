"""Shared recovery order for replay-safe modes that retain external-attempt evidence."""

from datetime import timedelta

from due.django.capabilities.authoring import DurableWorkPolicy
from due.django.evidence.attempts import (
    expire_repair_window,
    prune_resolved_attempts,
    resolve_confirmed_attempts,
)
from due.django.models import DurableWork
from due.django.runtime.coordination import database_now
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import RecoveryStep


def attempt_recovery_steps(
    model: type[DurableWork],
    policy: DurableWorkPolicy,
    *,
    retention: timedelta,
    derivation: tuple[RecoveryStep, ...],
    keep_started_evidence: bool = False,
) -> tuple[RecoveryStep, ...]:
    """Expire uncertainty before derivation; resolve receipts before pruning with the mode's retention rule."""
    return (
        RecoveryStep(
            RecoveryPhase.REPAIR_WINDOW,
            lambda _: expire_repair_window(model, window=policy.max_repair_window, batch_size=policy.batch_size),
        ),
        *derivation,
        RecoveryStep(
            RecoveryPhase.ATTEMPT_RESOLUTION,
            lambda _: resolve_confirmed_attempts(model, batch_size=policy.batch_size),
        ),
        RecoveryStep(
            RecoveryPhase.ATTEMPT_RETENTION,
            lambda _: prune_resolved_attempts(
                model,
                before=database_now() - retention,
                batch_size=policy.batch_size,
                keep_started_evidence=keep_started_evidence,
            ),
        ),
    )
