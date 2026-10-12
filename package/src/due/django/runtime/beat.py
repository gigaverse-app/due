"""Celery's persistent scheduler with bounded early deadline checks."""

from celery.beat import PersistentScheduler


class RecoveryScheduler(PersistentScheduler):
    """Keep Celery's early wake, then yield for the final deadline remainder.

    Celery subtracts 10 ms to compensate for timer drift. Once inside that
    window its negative result makes Service.start() skip sleeping entirely.
    A still-future deadline can sleep its remainder without granting early
    delivery authority. Due entries continue to return zero from tick().
    """

    def tick(self, *args, **kwargs) -> float:
        delay = super().tick(*args, **kwargs)
        # Repair only the service's sleep interval. Changing adjust() also
        # changes heap ordering and can delay a neighboring earlier deadline.
        return delay + 0.010 if delay < 0 else delay


def recovery_scheduler(configured: object, *, default: object = "due.django.runtime.beat:RecoveryScheduler") -> object:
    """Replace only Celery's stock scheduler; preserve application extensions."""
    if configured in ("celery.beat:PersistentScheduler", "celery.beat.PersistentScheduler", PersistentScheduler):
        return default
    return configured
