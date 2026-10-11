"""Evidence-only observation of uncertain operations, with shared delivery ownership.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

import logging
from abc import abstractmethod
from datetime import datetime, timedelta
from typing import ClassVar

from django.db import models
from django.db.models.functions import Now

from due.django.capabilities.operations import DurableExternalOperation, ExternalOperationWork
from due.django.integration import current_universe_scope
from due.django.models import DurableWorkState
from due.django.runtime._delivery import RecoveryLease
from due.django.runtime.coordination import database_now
from due.django.runtime.limits import (
    EXECUTION_SOFT_TIME_LIMIT_SECONDS,
    EXECUTION_TIME_LIMIT_SECONDS,
)
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import RecoveryPass, RecoveryStep

logger = logging.getLogger(__name__)


class ObservedExternalOperationWork(ExternalOperationWork):
    next_observation_at = models.DateTimeField(db_default=Now())
    observation_claimed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


def observation_index(name: str) -> models.Index:
    """Concrete adopters include this shared due-observation index in Meta."""
    return models.Index(
        fields=["next_observation_at", "id"], condition=models.Q(state=DurableWorkState.UNKNOWN), name=name
    )


class ObservedExternalOperation[OwnerT: models.Model, WorkT: ObservedExternalOperationWork, ResultT](
    DurableExternalOperation[OwnerT, WorkT, ResultT]
):
    observation_interval: ClassVar[timedelta] = timedelta(minutes=1)
    observation_related: ClassVar[tuple[str, ...]] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.policy.lease_duration <= timedelta(seconds=EXECUTION_TIME_LIMIT_SECONDS):
            raise ValueError("observation lease must outlive the worker hard limit")
        if self.observation_interval <= timedelta(0):
            raise ValueError("observation_interval must be positive")
        if not issubclass(self.model, ObservedExternalOperationWork):
            raise TypeError("observed operations require ObservedExternalOperationWork")

    @abstractmethod
    def observe_effect(self, work: WorkT) -> ResultT | None:
        """Return exactly correlated positive evidence; None means still unknown."""

    @property
    def observation_lease(self) -> RecoveryLease[WorkT]:
        return RecoveryLease(model=self.model, claimed_at="observation_claimed_at", duration=self.policy.lease_duration)

    def observations_due(self) -> models.QuerySet[WorkT]:
        return self.model.objects.filter(
            state=DurableWorkState.UNKNOWN, next_observation_at__lte=database_now()
        ).order_by("next_observation_at", "pk")

    def derivation_steps(self) -> tuple[RecoveryStep, ...]:
        return (*super().derivation_steps(), RecoveryStep(RecoveryPhase.OBSERVATION, self._recover_observations))

    def _recover_observations(self, recovery: RecoveryPass) -> int:
        # Only reserve and publish here. Slow inventory cannot hold up regular
        # claim recovery/dispatch; provider reads belong to bounded workers.
        return self.observation_lease.recover(
            self.observations_due(),
            publish=self._publish_observation,
            batch_size=self.policy.batch_size,
            recovery=recovery,
            phase=RecoveryPhase.OBSERVATION,
        )

    def _publish_observation(self, work_id: int, token: datetime) -> None:
        from due.django.runtime.registry import registration_for
        from due.django.runtime.tasks import observe_durable_work_task

        registration = registration_for(self.model)
        # Pin the budget in the message, as ordinary work publication does.
        # Celery annotations may override task defaults; they must not let this
        # worker outlive the lease whose duration was validated above.
        observe_durable_work_task.apply_async(
            args=(registration.name, work_id, token.isoformat()),
            queue=registration.queue,
            time_limit=EXECUTION_TIME_LIMIT_SECONDS,
            soft_time_limit=EXECUTION_SOFT_TIME_LIMIT_SECONDS,
        )

    def attempt_observation(self, work_id: int, delivery_token: str) -> bool:
        return self.observation_lease.attempt_execution(
            work_id, token=datetime.fromisoformat(delivery_token), run=self._observe
        )

    def _observe(self, work_id: int) -> None:
        queryset = self.model.objects.filter(pk=work_id, state=DurableWorkState.UNKNOWN)
        if self.observation_related:
            queryset = queryset.select_related(*self.observation_related)
        work = queryset.first()
        if work is None:
            return  # A webhook/receipt may have settled while this message waited.
        # Advance before I/O so inconclusive or unavailable rows yield their turn.
        # This clock schedules observation only; it cannot authorize a create.
        self.model.objects.filter(pk=work.pk, observation_claimed_at=work.observation_claimed_at).update(
            next_observation_at=database_now() + self.observation_interval
        )
        with current_universe_scope(work.work_universe()):
            try:
                result = self.observe_effect(work)
            except Exception:
                logger.warning(
                    "Observation unavailable for %s/%s", self.model._meta.label_lower, work.pk, exc_info=True
                )
                return
            # Only provider unavailability is inconclusive. Contradictory evidence
            # or failed local settlement must fail this worker visibly. The lease
            # still releases in RecoveryLease, and a committed receipt remains
            # repairable by the existing operation runtime without another read.
            if result is not None and self.record_observed_effect_evidence(work.pk, result):
                self.settle_from_applied_effect_evidence(work.pk)
