"""One recovery tick: named phases, per-identity isolation, one loud outcome.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cache
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from django.db import IntegrityError, NotSupportedError, models, transaction
from django.db.models import Count, Min, Q, QuerySet
from psycopg.pq import TransactionStatus
from sentry_sdk.scope import add_global_event_processor
from sentry_sdk.types import Event

from due._installed import NotInstalledError
from due.django.integration import aborts_recovery
from due.django.models import RecoveryDeferral
from due.django.runtime.metrics import (
    RecoveryOutcome,
    RecoveryPhase,
    prepare_recovery_series,
    record_recovery_completed,
    record_recovery_deferrals,
    record_recovery_failure,
    record_recovery_pass,
    record_recovery_stale_after,
    record_recovery_unclean,
)
from due.retry_delays import ExponentialDelay

logger = logging.getLogger(__name__)

#: How long a ticking effect may go without a clean tick: twice the 5-minute
#: cadence. A slower sweep passes ``stale_after`` of twice its own interval, and
#: the Beat-sweep tripwire checks it against the schedule.
DEFAULT_STALE_AFTER = timedelta(minutes=10)

#: First hold after one failure; doubles per consecutive failure up to the cap.
DEFERRAL_BASE = timedelta(minutes=1)
DEFERRAL_CAP = timedelta(hours=1)
_DEFERRAL_DELAY = ExponentialDelay(DEFERRAL_BASE, DEFERRAL_CAP)
#: One page slot in this many is reserved for identities whose hold has ended,
#: and at least one per page. New identities fill the rest first, so a backlog of
#: poisoned identities costs at most that share of a page, while each held one is
#: still retried in turn, oldest hold first.
RETRY_SHARE = 10
#: A hold whose identity has not failed for this long belongs to an identity no
#: page selects any more (settled, deleted, or no longer a candidate). Every held
#: identity that is still selected is retried, and refreshes its row, well within
#: it, so forgetting bounds the table without ever re-prioritizing a live
#: identity ahead of new ones.
DEFERRAL_FORGET_AFTER = timedelta(days=7)
#: Phases whose loops page through ``RecoveryPass.page``. A tick that paged
#: publishes a series for each, zero included; ``page`` refuses any other phase.
#: Work-table dispatch keeps its reservation instead of paging; native sweeps
#: that dispatch per identity page as DISPATCH.
DEFERRING_PHASES = (
    RecoveryPhase.DISCOVERY,
    RecoveryPhase.OBSERVATION,
    RecoveryPhase.REAP,
    RecoveryPhase.EVIDENCE_SETTLEMENT,
    RecoveryPhase.NONAPPLICATION,
    RecoveryPhase.DISPATCH,
)
#: Failures kept on the raised group. A shared cause (a broker or database
#: outage) fails every identity of a page; the first ones carry the diagnosis,
#: and an event holding hundreds of tracebacks would exceed Sentry's size limit
#: exactly when it matters. Every failure is still logged and counted.
MAX_REPORTED_FAILURES = 20


def _coordination_now() -> datetime:
    # The library's one clock seam (coordination imports this module, so import late).
    from due.django.runtime.coordination import database_now

    return database_now()


def deferral_backoff(failures: int) -> timedelta:
    # Saturates without computing the doubling: an identity that keeps failing
    # once an hour reaches 42 failures in about a day and a half, and
    # ``DEFERRAL_BASE * 2 ** 41`` overflows ``timedelta``.
    return _DEFERRAL_DELAY.at(failures - 1)


def ends_the_tick(error: BaseException, propagate: tuple[type[BaseException], ...] = ()) -> bool:
    """
    Whether this failure stops the whole tick instead of costing one identity.

    An unwired slot fails every identity alike, Celery's soft limit is an
    orderly-stop request, and a declared ``propagate`` type means the tick's own
    guarantees are gone. Helpers wrap errors (an advisory lock turns the soft
    limit into ``AdvisoryLockUnavailable`` from it), so the whole cause/context
    chain is inspected, not just the outer type. A step that raised a database
    error inside the caller's transaction without a savepoint of its own left
    that transaction unusable: every later identity would fail on it, so the
    original error ends the tick instead.
    """
    if _transaction_is_broken():
        return True
    stop_on = (NotInstalledError, SoftTimeLimitExceeded, *propagate)
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        # Application slots have their own exception identity. Configured fatal
        # errors must never become row deferrals or allow later phases to run.
        if isinstance(current, stop_on) or aborts_recovery(current):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _transaction_is_broken() -> bool:
    """Whether the current database transaction can run no further statement until it rolls back."""
    connection = transaction.get_connection()
    if connection.in_atomic_block and connection.needs_rollback:
        return True
    raw = connection.connection
    return raw is not None and raw.info.transaction_status == TransactionStatus.INERROR


class RecoveryIncomplete(ExceptionGroup):
    """A recovery tick ran every phase but isolated at least one failure."""

    #: The effect whose tick this is. Sentry groups one issue per effect on it
    #: (``group_recovery_reports``); the leaf exceptions are library frames
    #: shared by every effect, so default grouping would merge all of them.
    effect: str = ""


@dataclass
class _PhaseHolds:
    """The deferral rows of one phase as of the tick's clock."""

    #: Identities held out of this phase right now.
    waiting: set[str] = field(default_factory=set)
    #: Identities whose hold has ended, with its end: retried oldest hold first.
    ended: dict[str, datetime] = field(default_factory=dict)

    @property
    def known(self) -> set[str]:
        return self.waiting | self.ended.keys()

    def forget(self, identity: str) -> None:
        self.waiting.discard(identity)
        self.ended.pop(identity, None)


@dataclass
class RecoveryPass:
    """The failures one recovery tick of one effect isolated, in order. Start one with ``recovery_tick``."""

    effect: str
    #: Exception types that end the tick wherever they appear in a failure's
    #: cause chain, in addition to the library's own (see ``ends_the_tick``). A
    #: sweep holding session-level locks passes the session-loss error, so it
    #: never runs on without them.
    propagate: tuple[type[BaseException], ...] = ()
    errors: list[Exception] = field(default_factory=list)
    failures: int = 0
    _now: datetime | None = None
    #: Every deferral row of the effect, read once per tick on the first page.
    _holds: dict[RecoveryPhase, _PhaseHolds] | None = None
    #: Phases that paged this tick: only their isolated identities are deferred.
    _paged: set[RecoveryPhase] = field(default_factory=set)
    #: Phases whose failure counter this tick has created (at zero, before any failure).
    _counted: set[RecoveryPhase] = field(default_factory=set)

    @property
    def paged(self) -> bool:
        """Whether any phase of this tick paged, and so may hold identities."""
        return bool(self._paged)

    def clock(self) -> datetime:
        """The tick's coordination time, read once: its holds and its census judge against one instant."""
        if self._now is None:
            self._now = _coordination_now()
        return self._now

    def page(self, phase: RecoveryPhase, ordered_ids: QuerySet[Any, Any], size: int) -> list[Any]:
        """
        The next ``size`` identities of an ordered id query for this phase, held ones aside.

        Reads ``size`` plus the number of held identities, so holds never shorten
        the page, and works on any application queryset: ordering is untouched
        and no anti-join changes its plan. Identities never held come first.
        Identities whose hold has ended take a reserved share of
        ``size // RETRY_SHARE`` slots (at least one, so a page of fewer than
        ``RETRY_SHARE`` slots retries before it serves new identities), oldest
        hold first, and any slots new identities leave. Identities still held
        are skipped. A held identity the query no longer returns (settled,
        deleted, excluded by a fix) is released here. Failures in this phase
        defer from now on.
        """
        if phase not in DEFERRING_PHASES:
            raise ValueError(f"{phase.value} pages, so it must be listed in DEFERRING_PHASES for the census")
        holds = self._holds_for(phase)
        known = holds.known
        window = list(ordered_ids[: size + len(known)])
        unique = list(dict.fromkeys(window))
        if len(unique) != len(window):
            # A query that repeats an identity is wrong (a to-many join), but each
            # identity it returns is still owed: report it, and serve them once.
            self._record(phase, None, ValueError(f"the {phase.value} query returned an identity more than once"))
        self._release_unselected(phase, ordered_ids, absent=known - {str(identity) for identity in unique})
        fresh = [identity for identity in unique if str(identity) not in known]
        ended = sorted(
            (identity for identity in unique if str(identity) in holds.ended),
            key=lambda identity: holds.ended[str(identity)],
        )
        retried = ended[: max(1, size // RETRY_SHARE)]
        chosen = {*retried, *[*fresh, *ended[len(retried) :]][: size - len(retried)]}
        return [identity for identity in unique if identity in chosen]

    def run_page[IdentityT](
        self,
        phase: RecoveryPhase,
        ordered_ids: QuerySet[Any, IdentityT],
        size: int,
        step: Callable[[IdentityT], int],
    ) -> int:
        """Count successful transitions in one page, isolating and deferring each failed identity."""
        changed = 0
        for identity in self.page(phase, ordered_ids, size):
            with self.isolate(phase, identity):
                changed += step(identity)
        return changed

    def held(self, phase: RecoveryPhase, selection: QuerySet[Any, Any]) -> frozenset[str]:
        """
        The identities this phase holds out right now, for a loop that picks one identity at a time.

        :meth:`page` serves a batch of distinct identities. A loop that selects
        its next identity itself (and may serve one identity several times, as
        the membership sweep serves a large community batch after batch)
        excludes these instead. ``selection`` is the loop's flat identity query:
        a held identity it no longer returns is released, as :meth:`page` does.
        An identity whose hold has ended is not held: it is served in its normal
        order and released when it succeeds. Failures in this phase defer from
        now on.
        """
        if phase not in DEFERRING_PHASES:
            raise ValueError(f"{phase.value} holds identities, so it must be listed in DEFERRING_PHASES")
        holds = self._holds_for(phase)
        self._release_unselected(phase, selection, absent=set(holds.known))
        return frozenset(holds.waiting)

    @contextmanager
    def isolate(self, phase: RecoveryPhase, identity: object = None) -> Iterator[None]:
        """Run one identity's step (or a whole phase); record its failure and continue."""
        if phase not in self._counted:
            prepare_recovery_series(self.effect, phase)
            self._counted.add(phase)
        try:
            yield
            if identity is not None:
                self._release(phase, str(identity))
        except Exception as error:
            if ends_the_tick(error, self.propagate):
                raise
            self._record(phase, identity, error)

    def raise_if_failed(self) -> None:
        """Count the tick's outcome, then raise every failure it isolated. Ticks call this through ``recovery_tick``."""
        record_recovery_pass(self.effect, RecoveryOutcome.FAILED if self.errors else RecoveryOutcome.CLEAN)
        if self.errors:
            shown = "" if self.failures == len(self.errors) else f"; the first {len(self.errors)} are attached"
            incomplete = RecoveryIncomplete(
                f"{self.effect}: recovery isolated {self.failures} failure(s){shown}", self.errors
            )
            incomplete.effect = self.effect
            raise incomplete

    def _holds_for(self, phase: RecoveryPhase) -> _PhaseHolds:
        if self._holds is None:
            now = self.clock()
            self._holds = {deferring: _PhaseHolds() for deferring in DEFERRING_PHASES}
            rows = RecoveryDeferral.objects.filter(effect=self.effect).values_list("phase", "identity", "retry_at")
            for phase_value, identity, retry_at in rows:
                holds = self._holds.get(RecoveryPhase(phase_value)) if phase_value in RecoveryPhase else None
                if holds is None:
                    continue  # A phase this code no longer pages; forgotten in time.
                if retry_at > now:
                    holds.waiting.add(identity)
                else:
                    holds.ended[identity] = retry_at
        self._paged.add(phase)
        return self._holds[phase]

    def _record(self, phase: RecoveryPhase, identity: object, error: Exception) -> None:
        error.add_note(f"durable-work recovery: effect={self.effect} phase={phase.value} identity={identity!r}")
        self.failures += 1
        if len(self.errors) < MAX_REPORTED_FAILURES:
            self.errors.append(error)
        # A warning, not an error event: the group raised at the end of the tick
        # is the one Sentry event for every failure collected here.
        logger.warning(
            "Durable-work recovery isolated a failure; the rest of the tick continues",
            exc_info=error,
            extra={"effect": self.effect, "phase": phase.value, "identity": identity},
        )
        record_recovery_failure(self.effect, phase)
        if identity is not None and phase in self._paged:
            self._defer(phase, str(identity), repr(error))

    def _defer(self, phase: RecoveryPhase, identity: str, error: str) -> None:
        """Hold one isolated identity out of its phase; never mask the failure being recorded."""
        try:
            defer_identity(self.effect, phase, identity, error)
        except Exception as failure:
            if ends_the_tick(failure, self.propagate):
                raise
            # Without the row the identity is simply retried next tick, as before deferral existed.
            logger.exception(
                "Durable-work recovery could not defer an isolated identity",
                extra={"effect": self.effect, "phase": phase.value, "identity": identity},
            )

    def _release_unselected(self, phase: RecoveryPhase, ordered_ids: QuerySet[Any, Any], absent: set[str]) -> None:
        """
        Release holds on identities the query no longer returns at all.

        A hold is otherwise released only when its identity is served and
        succeeds. An identity a fix excludes from the selection (as #5297 did
        for file-less images) is never served again, and its hold would keep
        the deferred-identity alert firing until it is forgotten. One query,
        only when a held identity was missing from this page's window.
        """
        if not absent:
            return
        try:
            gone = absent - {str(identity) for identity in ordered_ids.filter(pk__in=absent)}
            if gone:
                RecoveryDeferral.objects.filter(effect=self.effect, phase=phase, identity__in=gone).delete()
        except NotSupportedError:
            return  # A union cannot be filtered; such holds are forgotten by age instead.
        except Exception as failure:
            if ends_the_tick(failure, self.propagate):
                raise
            # Best effort, like recording a hold: the page is served either way.
            logger.warning(
                "Durable-work recovery could not release holds on identities its query no longer returns",
                exc_info=failure,
                extra={"effect": self.effect, "phase": phase.value},
            )
            return
        holds = self._holds_for(phase)
        for identity in gone:
            holds.forget(identity)

    def _release(self, phase: RecoveryPhase, identity: str) -> None:
        holds = self._holds.get(phase) if self._holds is not None else None
        if holds is None or identity not in holds.known:
            return
        RecoveryDeferral.objects.filter(effect=self.effect, phase=phase, identity=identity).delete()
        holds.forget(identity)


@contextmanager
def recovery_tick(
    effect: str,
    *,
    propagate: tuple[type[BaseException], ...] = (),
    heartbeat: bool = True,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> Iterator[RecoveryPass]:
    """
    Run one recovery tick of ``effect`` and report it: the one way a tick starts and ends.

    Yields the tick's pass. When the body returns, every failure it isolated is
    raised as one ``RecoveryIncomplete`` and the tick counts as failed;
    otherwise it counts as clean and, with ``heartbeat``, advances the
    last-recovery timestamp that tells a stalled effect from an idle one. A
    failure that ends the tick propagates and the tick counts as aborted.
    Standalone loops (``heartbeat=False``) report the same way without
    claiming a whole tick ran. ``stale_after`` is how long the effect may go
    without a clean tick before the stale alert pages; it is published when a
    heartbeat tick starts, so it exists before the first clean one.
    """
    tick = RecoveryPass(effect, propagate=propagate)
    prepare_recovery_series(effect)
    if heartbeat:
        record_recovery_stale_after(effect, stale_after.total_seconds())
    try:
        yield tick
        if tick.paged:
            # Holds are visible wherever a tick pages, work-table or native, failed or clean.
            with tick.isolate(RecoveryPhase.CENSUS):
                publish_recovery_deferrals(effect, observed_at=tick.clock())
    except BaseException:
        record_recovery_pass(effect, RecoveryOutcome.ABORTED)
        _stamp_unclean(effect, heartbeat)
        raise
    if tick.errors:
        _stamp_unclean(effect, heartbeat)
    tick.raise_if_failed()
    if heartbeat:
        record_recovery_completed(effect, _coordination_now().timestamp())


def defer_identity(effect: str, phase: RecoveryPhase, identity: str, error: str) -> None:
    """Record one more consecutive failure and push ``retry_at`` out by the backoff."""
    with transaction.atomic():
        now = _coordination_now()
        current = (
            RecoveryDeferral.objects.select_for_update()
            .filter(effect=effect, phase=phase, identity=identity)
            .values_list("pk", "failures")
            .first()
        )
        if current is None:
            try:
                # A savepoint: an overlapping tick may record the same first failure.
                with transaction.atomic():
                    RecoveryDeferral.objects.create(
                        effect=effect,
                        phase=phase,
                        identity=identity,
                        first_failed_at=now,
                        last_failed_at=now,
                        retry_at=now + deferral_backoff(1),
                        last_error=error[:255],
                    )
                return
            except IntegrityError:
                current = (
                    RecoveryDeferral.objects.select_for_update()
                    .filter(effect=effect, phase=phase, identity=identity)
                    .values_list("pk", "failures")
                    .get()
                )
        failures = current[1] + 1
        RecoveryDeferral.objects.filter(pk=current[0]).update(
            failures=failures, last_failed_at=now, retry_at=now + deferral_backoff(failures), last_error=error[:255]
        )


def publish_recovery_deferrals(effect: str, *, observed_at: datetime | None = None) -> None:
    """Forget holds no page selects any more, then publish the held count and oldest age per phase."""
    observed_at = observed_at or _coordination_now()
    forget_before = observed_at - DEFERRAL_FORGET_AFTER

    def per_phase() -> dict[str, tuple[int, datetime, int]]:
        return {
            phase: (count, oldest, forgettable)
            for phase, count, oldest, forgettable in RecoveryDeferral.objects.filter(effect=effect)
            .values("phase")
            .annotate(
                count=Count("id"),
                oldest=Min("first_failed_at"),
                forgettable=Count("id", filter=Q(last_failed_at__lt=forget_before)),
            )
            .values_list("phase", "count", "oldest", "forgettable")
        }

    rows = per_phase()
    if any(
        forgettable for _count, _oldest, forgettable in rows.values()
    ):  # A tick with nothing to forget writes nothing.
        RecoveryDeferral.objects.filter(effect=effect, last_failed_at__lt=forget_before).delete()
        rows = per_phase()
    for phase in DEFERRING_PHASES:
        count, oldest, _forgettable = rows.get(phase.value, (0, None, 0))
        age = 0.0 if oldest is None else max(0.0, (observed_at - oldest).total_seconds())
        record_recovery_deferrals(effect, phase, count=count, oldest_age_seconds=age)


@contextmanager
def recovery_pass_for(effect: str, recovery: RecoveryPass | None) -> Iterator[RecoveryPass]:
    """
    Join the caller's tick, or own one for a standalone call.

    A loop called from a tick records into that tick; the same loop called on
    its own (an operator command, a test) still isolates each identity and then
    raises what it isolated, without advancing the heartbeat.
    """
    if recovery is not None:
        yield recovery
        return
    with recovery_tick(effect, heartbeat=False) as standalone:
        yield standalone


def deferral_identity_problem(primary_key: models.Field[Any, Any]) -> str | None:
    """Why identities with this primary key cannot be held in ``RecoveryDeferral``, or ``None``."""
    while isinstance(primary_key, models.ForeignKey):
        primary_key = primary_key.target_field
    stored = RecoveryDeferral._meta.get_field("identity")
    assert isinstance(stored, models.CharField) and stored.max_length is not None, "a bounded CharField"
    limit = stored.max_length
    if isinstance(primary_key, (models.IntegerField, models.UUIDField)):
        return None
    if isinstance(primary_key, models.CharField) and primary_key.max_length is not None:
        if primary_key.max_length <= limit:
            return None
    return (
        f"a deferred identity is stored as text of at most {limit} characters, and a "
        f"{type(primary_key).__name__} primary key does not fit it"
    )


def group_recovery_reports(event: Event, hint: dict[str, Any]) -> Event:
    """Group every incomplete tick of one effect into one Sentry issue, tagged with that effect."""
    exc_info = hint.get("exc_info")
    error = exc_info[1] if exc_info else None
    if isinstance(error, RecoveryIncomplete):
        # Per effect, and within it by the default grouping: a new effect opens a new
        # issue, and a new root cause in an effect is not swallowed by an archived one.
        event["fingerprint"] = ["durable-work-recovery-incomplete", error.effect, "{{ default }}"]
        tags = event.setdefault("tags", {})
        if isinstance(tags, dict):
            tags["durable_work.effect"] = error.effect
    return event


@cache
def install_recovery_reporting() -> None:
    """Install ``group_recovery_reports`` for this process; called once by ``wire()``."""
    add_global_event_processor(group_recovery_reports)


@dataclass(frozen=True)
class RecoveryStep:
    """One named phase of a capability's recovery tick."""

    phase: RecoveryPhase
    run: Callable[[RecoveryPass], int]

    @classmethod
    def paged[IdentityT](
        cls,
        phase: RecoveryPhase,
        candidates: Callable[[], QuerySet[Any, IdentityT]],
        action: Callable[[IdentityT], int],
        *,
        size: int,
    ) -> RecoveryStep:
        """Register a per-item action with library-owned paging, isolation and backoff.

        Supply an ordered, unsliced identity query and the product action.
        The action owns any transaction/recheck it needs. Conformance derives
        the fault seam from this same registration; tests need only product examples.
        """
        if size <= 0:
            raise ValueError("recovery page size must be positive")
        if phase not in DEFERRING_PHASES:
            raise ValueError(f"{phase.value} does not support paged recovery")
        return cls(phase, RecoveryPage(phase, candidates, action, size))


@dataclass(frozen=True)
class RecoveryPage[IdentityT]:
    """Executable page registration; runtime and conformance consume the same action."""

    phase: RecoveryPhase
    candidates: Callable[[], QuerySet[Any, IdentityT]]
    action: Callable[[IdentityT], int]
    size: int

    def __call__(self, recovery: RecoveryPass) -> int:
        return recovery.run_page(self.phase, self.candidates(), self.size, self.action)


def _stamp_unclean(effect: str, heartbeat: bool) -> None:
    """
    Record when a heartbeat tick ended without a clean outcome.

    The worker's wall clock, not the database's: an aborted tick may have lost
    its connection. Operators compare this with the clean heartbeat; clocks
    must be synchronized because close outcomes can otherwise appear reordered.
    """
    if heartbeat:
        record_recovery_unclean(effect, time.time())
