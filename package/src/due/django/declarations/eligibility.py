"""Database eligibility for reserved work, independent of ownership and settlement.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Q, QuerySet

from due.django.models import DurableWork


@dataclass(frozen=True, slots=True)
class ExecutionEligibility:
    """
    Park owed work until a DB predicate holds, optionally inspecting after a delay.

    ``condition`` is expressed against the work model and must perform no I/O.
    ``recheck_after`` permits inspection that long after ``available_at``, for
    prerequisites PostgreSQL cannot observe. It never bypasses retry backoff.
    Without it, recovery only dispatches rows satisfying the condition.

    This is a scheduling hint, not authorization: handlers must still validate
    mutable product facts and provider safety after acquiring their claim.
    """

    condition: Callable[[datetime], Q]
    recheck_after: timedelta | None = None

    def __post_init__(self) -> None:
        if self.recheck_after is not None and self.recheck_after <= timedelta(0):
            raise ValueError("eligibility recheck_after must be positive or None")

    def predicate(self, *, now: datetime) -> Q:
        """The same readiness/fallback rule for execution and aggregate metrics."""
        condition = self.condition(now)
        if self.recheck_after is not None:
            condition |= Q(available_at__lte=now - self.recheck_after)
        return condition

    def filter[WorkT: DurableWork](self, queryset: QuerySet[WorkT], *, now: datetime) -> QuerySet[WorkT]:
        # Filter the original indexed due selection, not an unbounded second
        # scan of retained work. Claim uses FOR UPDATE OF self, so nullable
        # related facts can be read without locking those rows in reverse order.
        # Use Exists for multivalued relations to preserve one row per obligation.
        return queryset.filter(self.predicate(now=now))
