"""
Bounded recovery and delivery reservation for canonical models.

dispatch_recovery_page() isolates per-row publication failures. RecoveryLease
adds queue-token reservation and consumption on an existing nullable timestamp.
recover() reserves a page for the periodic sweep; request_progress() reserves
one known identity after a commit through the same conditional update, so an
application's fast-path notification and its sweep share one delivery owner.
RecoveryRoute binds one lease to its owed-work predicate and worker task, so
producers, the sweep and the worker share one publication and token encoding.
The application owns its indexed due predicate and worker time bounds. This
lease does not change product state or prove external service replay; completion.py
separately guards native product publication.

Design and usage: docs/native-lifecycles.md.
"""

import logging
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

from asgiref.sync import sync_to_async
from celery import Task
from django.db import models
from django.db.models import Q, QuerySet
from django.utils import timezone

from due.django.runtime import transport
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

    def request_progress(
        self,
        due: QuerySet[RowT],
        identity: int,
        *,
        publish: Callable[[int, datetime], None],
        reserve_for: timedelta,
    ) -> None:
        """
        Offer one committed obligation an earlier delivery than the sweep would.

        Call it after persisting (or finding) the obligation, whoever created it.
        After the outermost commit, the identity is reserved with recover()'s
        own conditional update and published with its token. It is a no-op when
        ``due`` no longer selects the row (completed, stopped, or otherwise not
        owed) or when a delivery or execution already owns it. Inside this
        lease's own attempt_execution() it is a no-op for every identity, so a
        recovery path never needs to know how it was entered. Rollback
        publishes nothing.

        This is a notification, not durability. The committed row and the
        application's recovery sweep own the obligation: a crash before the
        callback leaves the row unreserved for the next tick, and a reserved
        message that is lost becomes recoverable after ``reserve_for``, which
        may be shorter than the lease so that a lost notification costs no more
        than the sweep's own debounce. A message consumed later than that is
        refused, exactly like an expired sweep delivery; the sweep redelivers.
        A publish delay says when to look again. It never authorizes replaying
        an operation whose outcome is uncertain; only the application's guarded
        transition in the worker can do that.
        """
        if due.model is not self.model or due.query.is_sliced:
            raise ValueError("progress requires an unsliced query for its canonical model")
        if not timedelta(0) < reserve_for <= self.duration:
            raise ValueError("a progress reservation must be positive and no longer than the lease")
        if self._key in _executing.get():
            # Recovery does not reschedule itself, for this identity or any
            # other it hands off to: whatever the execution leaves owed, the
            # next tick selects. Callers need no "entered from recovery" flag.
            return
        # The shared post-commit seam: a broker error is logged and keeps its
        # reservation, and can neither raise into the committed request nor
        # publish a second message for the same row. hold_nudges() drops the
        # notification before it reserves, leaving the row to the sweep.
        transport.notify_after_commit(
            ".".join(self._key),
            partial(self._reserve_and_publish, due, publish=publish, reserve_for=reserve_for),
            identity,
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


@dataclass(frozen=True, kw_only=True)
class RecoveryRoute[RowT: models.Model]:
    """
    One native obligation's delivery, bound once: lease, owed work and worker.

    Producers ask for progress by identity alone; the sweep publishes through the
    same route and the worker consumes through it, so the task arguments and token
    encoding have one owner. The task receives ``(identity, delivery_token)``;
    ``delivery_token`` is ISO-8601, or None in a message published before its
    lease existed. Every guarantee is RecoveryLease's: a route adds no state.

    One route per claim column: an owner with two independent workers needs two
    columns (two leases), or one worker that does both. The sweep is the retry:
    a Celery retry would resend a token the failed execution already released,
    so it could only ever be refused.
    """

    lease: RecoveryLease[RowT]
    #: Every row still owed, without the sweep's debounce. Unsliced and indexed.
    owed: Callable[[], QuerySet[RowT]]
    task: Task
    #: How long a progress message stays reserved beyond its countdown: queue
    #: slack, and no less than the sweep's own debounce. A lost notification is
    #: redelivered by the first sweep tick after ``delay + reservation_slack``.
    reservation_slack: timedelta
    #: The longest supported countdown. Its reservation must fit the lease at
    #: construction; each producer's delay is checked when requested.
    max_delay: timedelta

    def __post_init__(self) -> None:
        # Optional decorator option at the Celery boundary, not a Task attribute.
        if getattr(self.task, "autoretry_for", None):
            raise ValueError(f"{self.task.name}: drop autoretry_for; the recovery sweep redelivers failed work")
        # The lease only excludes duplicates while execution is bounded below it.
        # Celery treats zero as no hard limit, so checking only the upper bound
        # would accept a worker that can outlive its ownership.
        time_limit = self.task.time_limit
        if time_limit is None or not 0 < time_limit < self.lease.duration.total_seconds():
            raise ValueError(f"{self.task.name}: set a positive hard time_limit shorter than the lease duration")
        # Celery refuses publication for this configuration. Catch it before an
        # obligation is reserved and every progress request and sweep fails.
        soft_time_limit = self.task.soft_time_limit
        if soft_time_limit is not None and soft_time_limit > time_limit:
            raise ValueError(f"{self.task.name}: soft_time_limit must not exceed time_limit")
        if self.reservation_slack <= timedelta(0) or self.max_delay < timedelta(0):
            raise ValueError("reservation slack must be positive and max_delay non-negative")
        if self.max_delay + self.reservation_slack > self.lease.duration:
            raise ValueError("max_delay plus reservation slack must fit the lease")

    def request_progress(self, identity: int, *, delay: timedelta = timedelta(0)) -> None:
        """
        Ask the worker to look at ``identity`` after commit, ``delay`` from now.

        ``delay`` is only a broker countdown. It is not a deadline, backoff,
        lease or permission to replay an uncertain operation, and it must leave
        ``reservation_slack`` inside the lease.
        """
        if not timedelta(0) <= delay <= self.max_delay:
            raise ValueError("progress delay must be between zero and the route's max_delay")
        self.lease.request_progress(
            self.owed(),
            identity,
            publish=partial(self._publish, delay=delay),
            reserve_for=delay + self.reservation_slack,
        )

    async def arequest_progress(self, identity: int, *, delay: timedelta = timedelta(0)) -> None:
        """``request_progress`` from async code, on the thread that owns its transaction."""
        await sync_to_async(self.request_progress)(identity, delay=delay)

    def recover(
        self,
        due: QuerySet[RowT],
        *,
        batch_size: int,
        observe_outstanding: Callable[[int], None] | None = None,
        recovery: RecoveryPass | None = None,
    ) -> int:
        """Run the sweep: ``due`` is ``owed()`` narrowed by its debounce."""
        return self.lease.recover(
            due,
            publish=self._publish,
            batch_size=batch_size,
            observe_outstanding=observe_outstanding,
            recovery=recovery,
        )

    def attempt_delivery[ResultT](
        self, identity: int, delivery_token: str | None, *, run: Callable[[int], ResultT]
    ) -> bool:
        """Consume one delivered message; a stale or duplicate token runs nothing."""
        token = None if delivery_token is None else datetime.fromisoformat(delivery_token)
        return self.lease.attempt_execution(identity, token=token, run=run)

    def _publish(self, identity: int, token: datetime, *, delay: timedelta = timedelta(0)) -> None:
        if delay:
            self.task.apply_async(args=(identity, token.isoformat()), countdown=delay.total_seconds())
        else:
            self.task.apply_async(args=(identity, token.isoformat()))
