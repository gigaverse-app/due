"""Shared recovery order for replay-safe modes that retain external-attempt evidence."""

from dataclasses import dataclass
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
from due.django.runtime.recovery_pass import RecoveryPass, RecoveryStep


def attempt_recovery_steps(
    model: type[DurableWork],
    policy: DurableWorkPolicy,
    *,
    retention: timedelta,
    derivation: tuple[RecoveryStep, ...],
    keep_started_evidence: bool = False,
) -> tuple[RecoveryStep, ...]:
    """Expire uncertainty before derivation; resolve receipts before pruning with the mode's retention rule."""
    owner = AttemptRecovery(model, policy, retention, keep_started_evidence)
    return (
        RecoveryStep(RecoveryPhase.REPAIR_WINDOW, owner.expire),
        *derivation,
        RecoveryStep(RecoveryPhase.ATTEMPT_RESOLUTION, owner.resolve),
        RecoveryStep(RecoveryPhase.ATTEMPT_RETENTION, owner.prune),
    )


@dataclass(frozen=True)
class AttemptRecovery:
    """Library factory binding: execution and proof matching retain the same inputs."""

    model: type[DurableWork]
    policy: DurableWorkPolicy
    retention: timedelta
    keep_started_evidence: bool = False

    def expire(self, recovery: RecoveryPass) -> int:
        return expire_repair_window(self.model, window=self.policy.max_repair_window, batch_size=self.policy.batch_size)

    def resolve(self, recovery: RecoveryPass) -> int:
        return resolve_confirmed_attempts(self.model, batch_size=self.policy.batch_size)

    def prune(self, recovery: RecoveryPass) -> int:
        return prune_resolved_attempts(
            self.model,
            before=database_now() - self.retention,
            batch_size=self.policy.batch_size,
            keep_started_evidence=self.keep_started_evidence,
        )
