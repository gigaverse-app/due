"""Internal delivery arbitration for work-table observations; not a native lifecycle API."""

import logging
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

from django.db import models
from django.db.models import Q, QuerySet
from django.utils import timezone

from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import RecoveryPass, recovery_pass_for

logger = logging.getLogger(__name__)

# Leases whose attempt_execution() is running in this context. asgiref carries
# context variables across sync_to_async/async_to_sync, so an async execution
# body is covered too.
_executing: ContextVar[frozenset[tuple[str, str]]] = ContextVar("recovery_lease_executing", default=frozenset())


def dispatch_recovery_page[RowT: models.Model, DispatchResultT](
    due: QuerySet[RowT],
    *,
    dispatch: Callable[[int], DispatchResultT],
    batch_size: int,
    observe_outstanding: Callable[[int], None] | None = None,
    recovery: RecoveryPass | None = None,
    phase: RecoveryPhase = RecoveryPhase.DISPATCH,
) -> int:
    """
    Count normal delivery returns; explicit False means no ownership was acquired.

    Each identity is isolated: neither a poisoned row nor an ambiguous
    publication may abandon later identities, and the canonical lifecycle still
    owns retry eligibility. A failed identity is recorded in the caller's tick,
    or, called on its own, raised after the whole page.
    """
    if batch_size < 1 or due.query.is_sliced:
        raise ValueError("recovery requires an unsliced query and a positive batch size")
    if observe_outstanding is not None:
        observe_outstanding(due.count())
    identities = list(due.values_list("pk", flat=True)[:batch_size])
    delivered = 0
    with recovery_pass_for(due.model._meta.label_lower, recovery) as tick:
        for identity in identities:
            with tick.isolate(phase, identity):
                delivered += dispatch(identity) is not False
    return delivered


@dataclass(frozen=True, kw_only=True)
class RecoveryLease[RowT: models.Model]:
    """
    Reserve delivery on an existing owner without duplicating its lifecycle.

    One nullable timestamp is the fencing token for both queue and execution.
    Execution replaces the queue token, so duplicate deliveries cannot enter.
    The lease bounds exclusion, not provider I/O: the domain must still make
    reconciliation replay-safe and bound worker execution below this duration.
    """

    model: type[RowT]
    claimed_at: str
    duration: timedelta

    def __post_init__(self) -> None:
        field = self.model._meta.get_field(self.claimed_at)
        if not isinstance(field, models.DateTimeField) or not field.null:
            raise ValueError("recovery claim must be a nullable DateTimeField")
        if self.duration <= timedelta(0):
            raise ValueError("recovery lease duration must be positive")

    def available(self, due: QuerySet[RowT]) -> QuerySet[RowT]:
        from due.django.runtime.coordination import database_now

        if due.model is not self.model or due.query.is_sliced:
            raise ValueError("recovery requires an unsliced query for its canonical model")
        return due.filter(
            Q(**{f"{self.claimed_at}__isnull": True}) | Q(**{f"{self.claimed_at}__lte": database_now() - self.duration})
        )

    def recover(
        self,
        due: QuerySet[RowT],
        *,
        publish: Callable[[int, datetime], None],
        batch_size: int,
        observe_outstanding: Callable[[int], None] | None = None,
        recovery: RecoveryPass | None = None,
        phase: RecoveryPhase = RecoveryPhase.DISPATCH,
    ) -> int:
        from due.django.runtime.coordination import require_autocommit

        require_autocommit()
        available = self.available(due)
        if observe_outstanding is not None:
            observe_outstanding(due.count())
        return dispatch_recovery_page(
            available,
            dispatch=partial(self._reserve_and_publish, due, publish=publish),
            batch_size=batch_size,
            recovery=recovery,
            phase=phase,
        )

    @property
    def _key(self) -> tuple[str, str]:
        return (self.model._meta.label_lower, self.claimed_at)

    def _reserve_and_publish(
        self,
        due: QuerySet[RowT],
        identity: int,
        *,
        publish: Callable[[int, datetime], None],
        reserve_for: timedelta | None = None,
    ) -> bool:
        from due.django.runtime.coordination import database_now

        # The token is its own expiry: available() and attempt_execution() both
        # treat a reservation as live for ``duration`` after the token, so a
        # shorter reservation is a token dated that much earlier.
        token = database_now() - (self.duration - (reserve_for or self.duration))
        # Re-evaluate the canonical due predicate and ownership in the same
        # conditional UPDATE. Competing ticks and requests cannot both reserve
        # this row, and an execution in progress holds it too.
        if not self.available(due.filter(pk=identity)).update(**{self.claimed_at: token}):
            return False
        # An exception may follow broker acceptance: keep the reservation
        # until consumption or expiry, rather than publishing a duplicate.
        publish(identity, token)
        return True

    def attempt_execution[ResultT](
        self, identity: int, *, run: Callable[[int], ResultT], token: datetime | None = None
    ) -> bool:
        from due.django.runtime.coordination import database_now, require_autocommit

        require_autocommit()
        execution = database_now()
        rows = self.model.objects.filter(pk=identity)
        if token is None:
            owned = self.available(rows)  # Legacy messages acquire the same lease.
        else:
            if timezone.is_naive(token):
                raise ValueError("delivery token must be timezone-aware")
            owned = rows.filter(**{self.claimed_at: token, f"{self.claimed_at}__gt": execution - self.duration})
            execution = max(execution, token + timedelta(microseconds=1))
        if not owned.update(**{self.claimed_at: execution}):
            return False
        entered = _executing.set(_executing.get() | {self._key})
        try:
            run(identity)
        finally:
            _executing.reset(entered)
            # A resumed old execution cannot release its successor. Hard worker
            # death skips this block; expiry still makes the owner recoverable.
            rows.filter(**{self.claimed_at: execution}).update(**{self.claimed_at: None})
        return True
