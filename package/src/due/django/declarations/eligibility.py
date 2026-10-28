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

    ``condition`` is a static Q or a time-dependent callable expressed against
    the work model. Callables must perform no I/O. Static predicates without
    a fallback let claims read database time with the locked row.
    ``recheck_after`` permits inspection that long after ``available_at``, for
    prerequisites PostgreSQL cannot observe. It never bypasses retry backoff.
    Without it, recovery only dispatches rows satisfying the condition.

    This is a scheduling hint, not authorization: handlers must still validate
    mutable product facts and provider safety after acquiring their claim.
    """

    condition: Q | Callable[[datetime], Q]
    recheck_after: timedelta | None = None

    def __post_init__(self) -> None:
        if self.recheck_after is not None and self.recheck_after <= timedelta(0):
            raise ValueError("eligibility recheck_after must be positive or None")

    @property
    def requires_time(self) -> bool:
        """Whether evaluating this gate requires a concrete timestamp."""
        return not isinstance(self.condition, Q) or self.recheck_after is not None

    def predicate(self, *, now: datetime | None = None) -> Q:
        """The same readiness/fallback rule for execution and aggregate metrics."""
        if now is None and self.requires_time:
            raise ValueError("time-dependent eligibility requires now")
        if isinstance(self.condition, Q):
            condition = self.condition
        else:
            assert now is not None
            condition = self.condition(now)
        if self.recheck_after is not None:
            assert now is not None
            return condition | Q(available_at__lte=now - self.recheck_after)
        return condition

    def filter[WorkT: DurableWork](self, queryset: QuerySet[WorkT], *, now: datetime | None = None) -> QuerySet[WorkT]:
        # Filter the original indexed due selection, not an unbounded second
        # scan of retained work. Claim uses FOR UPDATE OF self, so nullable
        # related facts can be read without locking those rows in reverse order.
        # Use Exists for multivalued relations to preserve one row per obligation.
        return queryset.filter(self.predicate(now=now))
