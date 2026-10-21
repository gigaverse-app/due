"""Typed application capabilities and atomic obligation producers.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta
from enum import StrEnum
from functools import partial
from typing import Any, ClassVar, Self, final
from uuid import UUID

from django.db import OperationalError, connection, models, transaction
from django.utils.timezone import is_naive
from psycopg.errors import LockNotAvailable

from due.django.declarations.capture import Capture
from due.django.declarations.dependents import Dependent
from due.django.declarations.eligibility import ExecutionEligibility
from due.django.declarations.mirrors import Outcome
from due.django.models import (
    ACTIVE_WORK_STATES,
    DurableWork,
    DurableWorkState,
    UniverseDurableWork,
)
from due.django.runtime import transport
from due.django.runtime.coordination import (
    CLAIMABLE_STATES,
    ContinueAfter,
    NowFn,
    Publisher,
    ReopenAfter,
    WakeAction,
    WorkExecutionContext,
    WorkFailureDisposition,
    claim_work,
    database_now,
    dispatch_due_work,
    due_work,
    prune_completed_work,
    publish_work_census,
    reap_expired_work,
    require_autocommit,
    require_fenced_settlement,
    run_work,
    supersede_work,
    wake_work,
)
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import (
    RecoveryPass,
    RecoveryStep,
    deferral_identity_problem,
    recovery_tick,
)
from due.errors import CompletionStatus, WorkIncomplete
from due.observability import capture_exception_safely
from due.observations import AppliedEffectEvidence, effect_result
from due.retry_delays import ExponentialDelay

logger = logging.getLogger(__name__)

FAILED_TERMINAL_STATES = (
    DurableWorkState.TERMINAL_FAILED.value,
    DurableWorkState.EXHAUSTED.value,
)


class WorkOwnerBusy(Exception):
    """A non-waiting producer must retry after the current owner transaction."""


class CompletionErrors(StrEnum):
    """What a synchronous caller does with a handler exception from its own attempt."""

    # Propagate it, after the normal durable failure classification.
    RAISE = "raise"
    # Report it and return the revision's status; recovery owns the debt.
    OWE = "owe"


@dataclass(frozen=True, slots=True)
class Completion[WorkT: DurableWork]:
    """A synchronous caller's view of one admitted revision; see complete_or_defer()."""

    status: CompletionStatus
    work: WorkT | None
    error: Exception | None = None

    @property
    def completed(self) -> bool:
        return self.status is CompletionStatus.COMPLETED


@dataclass(frozen=True, slots=True)
class DurableWorkPolicy:
    """Shared execution and retention policy declared once per capability."""

    lease_duration: timedelta = timedelta(minutes=5)
    retry_delay: timedelta | ExponentialDelay = timedelta(minutes=1)
    max_rearms: int | None = 3
    retention: timedelta | None = timedelta(days=7)
    batch_size: int = 100
    max_repair_window: timedelta | None = None

    def __post_init__(self) -> None:
        if self.lease_duration <= timedelta(0) or self.delay_at(0) < timedelta(0):
            raise ValueError("lease must be positive and retry delay nonnegative")
        if (self.max_rearms is not None and self.max_rearms < 0) or self.batch_size <= 0:
            raise ValueError("max_rearms must be nonnegative or None, and batch_size positive")
        if self.retention is not None and self.retention <= timedelta(0):
            raise ValueError("retention must be positive or None")

        if self.max_repair_window is not None and self.max_repair_window <= timedelta(0):
            raise ValueError("max_repair_window must be positive or None")

    def delay_at(self, step: int) -> timedelta:
        """Evaluate timing before a retry transition; never advance its counter."""
        return self.retry_delay.at(step) if isinstance(self.retry_delay, ExponentialDelay) else self.retry_delay


class ReplayContract(StrEnum):
    """What an expired claim permits the runtime to execute again."""

    REPLAY_SAFE = "replay_safe"
    EVIDENCE_REQUIRED = "evidence_required"


class AppliedEvidenceContract(StrEnum):
    """Where reconciliation finds permanent proof that work is complete."""

    CANONICAL_OWNER_STATE = "canonical_owner_state"
    RETAINED_WORK_ROW = "retained_work_row"


@dataclass(frozen=True, slots=True)
class DiscoveryHorizon:
    """Only candidates whose ``field`` is within ``age`` of now are derived."""

    field: str
    age: timedelta

    def __post_init__(self) -> None:
        if self.age <= timedelta(0):
            raise ValueError("discovery horizon age must be positive")


@dataclass(frozen=True, slots=True)
class Discovery[OwnerT: models.Model, WorkT: UniverseDurableWork]:
    """
    The canonical missing-work query and its under-lock desired input.

    A discovery walks candidates in the application queryset's declared order, at
    ``page_size`` per recovery tick. Without a bound, historical rows queue
    current work behind them for as long as the backlog takes to drain. Declare
    a ``horizon`` that limits candidates by age, or state in
    ``unbounded_because`` why the query is already bounded by product facts.
    The candidate query must be ordered; a page over an unordered set is not
    a stable selection.
    """

    candidates: Callable[[], models.QuerySet[OwnerT]]
    desired_for: Callable[[OwnerT, WorkT | None], WorkT | None]
    page_size: int
    applied_evidence: AppliedEvidenceContract
    horizon: DiscoveryHorizon | None = None
    unbounded_because: str | None = None

    def __post_init__(self) -> None:
        if self.page_size <= 0:
            raise ValueError("discovery page_size must be positive")
        if not callable(self.candidates) or not callable(self.desired_for):
            raise TypeError("discovery requires candidate and desired-state callables")
        if (self.horizon is None) == (self.unbounded_because is None):
            raise ValueError("discovery must declare exactly one of horizon or unbounded_because")
        if self.unbounded_because is not None and len(self.unbounded_because.strip()) < 20:
            raise ValueError("unbounded_because must state the product fact that bounds the candidate set")

    def bounded_candidates(self, *, now: datetime) -> models.QuerySet[OwnerT]:
        """The production candidate set after the declared horizon, ordered."""
        queryset = self.candidates()
        if not queryset.ordered:
            raise ValueError("discovery candidates must be an ordered queryset")
        if self.horizon is not None:
            queryset = queryset.filter(**{f"{self.horizon.field}__gte": now - self.horizon.age})
        return queryset


@dataclass(frozen=True, slots=True)
class DurableWorkCapability[WorkT: UniverseDurableWork, ResultT = None](ABC):
    """Standard execution and recovery contract for typed-work capabilities."""

    model: type[WorkT]
    nudge: Publisher
    policy: DurableWorkPolicy
    eligibility: ExecutionEligibility | None = dataclass_field(default=None, kw_only=True)
    wakes: tuple[Dependent[WorkT], ...] = dataclass_field(default=(), kw_only=True)
    execution_contract: ClassVar[ReplayContract] = ReplayContract.REPLAY_SAFE
    supports_repair_window: ClassVar[bool] = False
    _database_only: ClassVar[bool] = False
    #: Whether other code may wake this work; a capability whose rows are claimed by another's protocol opts out.
    wakeable: ClassVar[bool] = True

    # Opt in for registered Celery transport. Bound broker/worker timer storage;
    # PostgreSQL owns long deadlines, and recovery needs no hint receipts.
    deadline_wakeup_horizon: ClassVar[timedelta | None] = None

    def _enqueue_deadline_hint(self, work_id: int) -> bool:
        require_autocommit()
        if self.deadline_wakeup_horizon is not None:
            now = database_now()
            work = (
                self._deadline_candidates()
                .filter(
                    pk=work_id,
                    available_at__gt=now,
                    available_at__lte=now + self.deadline_wakeup_horizon,
                )
                .only("pk", "desired_revision", "available_at")
                .first()
            )
            if work is not None:
                self._publish_deadline_hint(work)
                return True
        return False

    def _publish_deadline_hint(self, work: WorkT) -> None:
        from due.django.runtime.registry import registration_for
        from due.django.runtime.tasks import wake_scheduled_work_task

        registration = registration_for(self.model)
        wake_scheduled_work_task.apply_async(
            args=(registration.name, work.pk, work.desired_revision, work.available_at.isoformat()),
            eta=work.available_at,
            # Celery supports this Kombu publish option; celery-types omits it.
            delivery_mode=1,  # pyrefly: ignore [unexpected-keyword]
            queue=registration.queue,
            time_limit=registration.execution_time_limit,
            soft_time_limit=registration.execution_soft_time_limit,
        )

    def deliver_deadline_hint(self, work_id: int, revision: int, available_at: datetime) -> None:
        require_autocommit()
        # A hint grants no authority. Even a matching hint must pass the ordinary
        # atomic due-time/eligibility claim and fenced delivery protocol below.
        if (
            self._deadline_candidates()
            .filter(
                pk=work_id,
                desired_revision=revision,
                available_at=available_at,
                available_at__lte=database_now(),
            )
            .exists()
        ):
            self.attempt_execution(work_id, expected_revision=revision)

    def _wake_near_deadlines(self, recovery: RecoveryPass) -> int:
        assert self.deadline_wakeup_horizon is not None
        now = database_now()
        upcoming = (
            self._deadline_candidates()
            .filter(
                available_at__gt=now,
                available_at__lte=now + self.deadline_wakeup_horizon,
            )
            .only("pk", "desired_revision", "available_at")
            .order_by("available_at", "pk")[: self.policy.batch_size]
        )
        sent = 0
        for work in upcoming:
            # Optional hints deliberately have no persisted RecoveryDeferral:
            # ordinary due dispatch ran first and remains the liveness owner.
            with recovery.isolate(RecoveryPhase.DEADLINE_WAKEUP, work.pk):
                self._publish_deadline_hint(work)
                sent += 1
        return sent

    def _deadline_candidates(self) -> models.QuerySet[WorkT]:
        return self.model.objects.filter(state__in=CLAIMABLE_STATES)

    def __post_init__(self) -> None:
        if self.deadline_wakeup_horizon is not None and not (
            timedelta(0) < self.deadline_wakeup_horizon <= timedelta(minutes=5)
        ):
            raise ValueError("deadline_wakeup_horizon must be positive and at most five minutes")
        if self.policy.max_repair_window is not None and not self.supports_repair_window:
            raise ValueError("max_repair_window is only supported by replay-safe projections and scheduled actions")
        expected = (
            ReplayContract.EVIDENCE_REQUIRED if self.model.requires_effect_evidence else ReplayContract.REPLAY_SAFE
        )
        if self.execution_contract is not expected:
            raise ValueError(
                "only replay-safe work may use replay-safe capabilities; operations require their evidence execution mode"
            )
        dependents = [dependent.work for dependent in self.wakes]
        if len(set(dependents)) != len(dependents):
            raise ValueError("declare each dependent once")
        for model in dependents:
            if model is self.model:
                raise ValueError("a capability cannot wake its own work")
            if model.requires_effect_evidence:
                raise ValueError("an occurrence cannot be superseded; wake only replay-safe dependents")

    @abstractmethod
    def apply(self, work: WorkT, context: WorkExecutionContext[WorkT]) -> ResultT | ContinueAfter:
        """Perform provider I/O outside transactions and return an ordinary result."""

    def finalize_success(self, work: WorkT, result: ResultT, /) -> ReopenAfter | None:
        """Publish under the fence; return any inherited same-revision repair decision."""

    def classify_error(self, error: Exception) -> WorkFailureDisposition:
        """Retry within policy by default; override for definite domain rejection."""
        return WorkFailureDisposition.RETRYABLE

    def finalize_terminal(self, work: WorkT, error: Exception) -> None:
        """Optionally record domain rejection in the kernel's fenced transaction."""

    def finalize_unknown(self, work: WorkT) -> None:
        """Publish uncertainty without claiming either provider failure or success."""

    def finalize_retry(self, work: WorkT, error: Exception) -> None:
        """Optionally mirror retry/exhaustion into product state under its fence."""

    @property
    def on_success(self) -> Outcome[WorkT, ResultT] | None:
        """Optional declared product update, before the explicit hook."""
        return None

    @property
    def on_unknown(self) -> Outcome[WorkT, None] | None:
        """Optional declared product update, before the explicit hook."""
        return None

    @property
    def on_retry(self) -> Outcome[WorkT, Exception] | None:
        """Optional declared product update, before the explicit hook."""
        return None

    @property
    def on_exhausted(self) -> Outcome[WorkT, Exception] | None:
        """Optional declared product update, before the explicit hook."""
        return None

    @property
    def on_terminal(self) -> Outcome[WorkT, Exception] | None:
        """Optional declared product update, before the explicit hook."""
        return None

    def _finalize_success(self, work: WorkT, result: ResultT) -> ReopenAfter | None:
        outcome = self.on_success
        if outcome is not None:
            outcome(work, result)
        repair = self.finalize_success(work, result)
        # An occurrence's success is its applied receipt: _finalize_applied wakes
        # dependents once, for live settlement and receipt repair alike.
        if not self.model.requires_effect_evidence:
            self._wake_dependents(work)
        return repair

    def _finalize_unknown(self, work: WorkT) -> None:
        outcome = self.on_unknown
        if outcome is not None:
            outcome(work, None)
        self.finalize_unknown(work)
        self._wake_dependents(work)

    def _finalize_terminal(self, work: WorkT, error: Exception) -> None:
        outcome = self.on_terminal
        if outcome is not None:
            outcome(work, error)
        self.finalize_terminal(work, error)
        self._wake_dependents(work)

    def _finalize_retry(self, work: WorkT, error: Exception) -> None:
        exhausted = work.state == DurableWorkState.EXHAUSTED
        outcome = self.on_exhausted if exhausted else self.on_retry
        if outcome is not None:
            outcome(work, error)
        self.finalize_retry(work, error)
        # A retry still owes its effect and tells a dependent nothing new;
        # exhaustion is a settlement a dependent must see.
        if exhausted:
            self._wake_dependents(work)

    def _wake_dependents(self, work: WorkT) -> None:
        """Last in settlement, after every hook: each dependent row is the transaction's last lock."""
        for dependent in self.wakes:
            dependent_id = dependent.via(work)
            if dependent_id is not None:
                _wake(dependent.work, dependent_id, partial(_enqueue_registered, dependent.work))

    def wake(self, work_id: int) -> WakeAction:
        """
        Make this existing work read facts committing in the caller's transaction.

        Use it wherever new facts commit: a command, a webhook, a lifecycle hook.
        A settlement of other work declares ``wakes=`` on that capability instead.
        Take every other row lock first; this locks only the work row. The
        notification follows the outermost commit, and recovery covers a lost
        one. See the library guide's "Waking dependent work" for each state.
        """
        if not self.wakeable:
            raise TypeError(f"{type(self).__name__} work is claimed by its producer's protocol; it cannot be woken")
        return _wake(self.model, work_id, self.enqueue_execution)

    @property
    def work_name(self) -> str:
        """The work model's label: notification, metric and log identity."""
        return self.model._meta.label_lower

    def retry_delay_for(self, work: WorkT) -> timedelta:
        return self.policy.delay_at(work.rearm_count)

    def attempt_execution(
        self, work_id: int, delivery_token: UUID | None = None, *, expected_revision: int | None = None
    ) -> bool:
        transitioned = run_work(
            self.model,
            work_id,
            handler=self.apply,
            classify_error=self.classify_error,
            lease_duration=self.policy.lease_duration,
            retry_delay=self.policy.delay_at(0),
            max_rearms=self.policy.max_rearms,
            delivery_token=delivery_token,
            expected_revision=expected_revision,
            finalize_success=self._finalize_success,
            finalize_terminal=self._finalize_terminal,
            finalize_unknown=self._finalize_unknown,
            finalize_retry=self._finalize_retry,
            retry_delay_for=self.retry_delay_for,
            database_only=self._database_only,
            eligibility=self.eligibility,
        )
        if transitioned and self.deadline_wakeup_horizon is not None:
            # Settlement committed before this disposable notification. Failure
            # cannot undo it or spend retry budget; the sweep still owns liveness.
            transport.notify_after_commit(self.work_name, self._enqueue_deadline_hint, work_id)
        return transitioned

    def enqueue_execution(self, work_id: int) -> None:
        require_autocommit()
        if self._enqueue_deadline_hint(work_id):
            return
        claim = claim_work(
            self.model, work_id, lease_duration=self.policy.lease_duration, queued=True, eligibility=self.eligibility
        )
        if claim is not None:
            self.nudge(work_id, claim.token)

    def execute_now(self, work: WorkT) -> bool:
        """
        Attempt newly requested work here, taking over its queued delivery.

        Application code may need synchronous completion after its product
        transaction commits. Read the current reservation here rather than
        asking the application to choose a fencing token. Ordinary broker
        deliveries must keep their captured token and use attempt_execution().
        """
        require_autocommit()
        current = (
            self.model.objects.only("state", "fencing_token")
            .filter(pk=work.pk, desired_revision=work.desired_revision)
            .first()
        )
        if current is None:
            return False
        token = current.fencing_token if current.state == DurableWorkState.QUEUED else None
        return self.attempt_execution(work.pk, token, expected_revision=work.desired_revision)

    def complete(self, work: WorkT) -> None:
        """
        Complete this requested revision now or raise, using the normal execution path.

        This does not wait for another worker, bypass backoff or replay unknown
        operations. Handler exceptions retain their normal classification and
        propagation; non-completion raises WorkIncomplete. A successful return
        proves committed finalization, not the absence of older remote effects.
        """
        require_autocommit()
        self._require_persisted(work)
        if self.completion_status(work) is CompletionStatus.COMPLETED:
            return
        self.execute_now(work)
        # Execution can successfully defer or reopen work. Its Boolean is not
        # a completion receipt; check the exact requested revision after commit.
        status = self.completion_status(work)
        if status is not CompletionStatus.COMPLETED:
            raise WorkIncomplete(
                f"{self.work_name}:{work.pk} revision {work.desired_revision} did not complete: {status}",
                status=status,
            )

    def complete_or_defer(
        self, work: WorkT | None, *, errors: CompletionErrors = CompletionErrors.RAISE
    ) -> Completion[WorkT]:
        """
        Attempt an admitted revision once, after its admission commits; report where it stands.

        This is the synchronous caller's single post-admission step. Pass the
        producer's return value as is: None reports NOT_ADMITTED (for example
        a legacy-owned owner). Admit with notify_worker=False so the fast nudge
        does not race this attempt.

        Inside an enclosing transaction the admission has not committed, so no
        provider call may happen yet: this registers the worker notification
        the producer suppressed and reports DEFERRED. Otherwise it runs
        complete(). Every incomplete outcome is a status, never WorkIncomplete,
        because the committed obligation is still owned by recovery; the caller
        decides which statuses its contract accepts (a latest-wins projection
        accepts SUPERSEDED). Handler exceptions propagate under RAISE. Under OWE
        they are reported and returned with the revision's resulting status.
        """
        if work is None:
            return Completion(CompletionStatus.NOT_ADMITTED, None)
        self._require_persisted(work)
        if connection.in_atomic_block or not connection.get_autocommit():
            # INVARIANT: no provider I/O before the admission commits. The
            # notification runs after the outermost commit, or never on rollback.
            transport.notify_after_commit(self.work_name, self.enqueue_execution, work.pk)
            return Completion(CompletionStatus.DEFERRED, work)
        try:
            self.complete(work)
        except WorkIncomplete as incomplete:
            return Completion(incomplete.status, work)
        except Exception as error:
            if errors is CompletionErrors.RAISE:
                raise
            capture_exception_safely(error)
            logger.warning(
                "Synchronous durable-work attempt failed; recovery owns the committed revision",
                exc_info=error,
                extra={"effect": self.work_name, "work_id": work.pk},
            )
            return Completion(self.completion_status(work), work, error)
        return Completion(CompletionStatus.COMPLETED, work)

    def completion_status(self, work: WorkT) -> CompletionStatus:
        """Read the committed status of exactly this requested revision."""
        current = (
            self.model.objects.filter(pk=work.pk).values_list("state", "desired_revision", "applied_revision").first()
        )
        if current is None:
            return CompletionStatus.MISSING
        state, desired_revision, applied_revision = current
        if desired_revision != work.desired_revision:
            return CompletionStatus.SUPERSEDED
        if state == DurableWorkState.SUCCEEDED and applied_revision == desired_revision:
            return CompletionStatus.COMPLETED
        if state in (DurableWorkState.QUEUED, DurableWorkState.CLAIMED):
            return CompletionStatus.IN_PROGRESS
        if state in FAILED_TERMINAL_STATES:
            return CompletionStatus.STOPPED
        return CompletionStatus.OWED

    def _require_persisted(self, work: WorkT) -> None:
        if type(work) is not self.model or work.pk is None:
            raise ValueError(f"completion requires a persisted {self.model.__name__}")

    def settle_expired_claim(self, work: WorkT) -> bool:
        """Optional durable-evidence settlement under the reaper's owner/work locks."""
        return False

    def due_work(self) -> models.QuerySet[WorkT]:
        """The actual eligible selection, shared with the conformance harness."""
        return due_work(self.model, eligibility=self.eligibility)

    def recover(self) -> int:
        """
        Run one recovery tick: every declared phase, in order, each identity isolated.

        Derivation reads product facts, which one bad row can poison; reap and
        dispatch read only recorded work. No failure in one phase or identity
        stops another (see ``RecoveryPass``). The tick raises what it isolated
        after the last phase, and only a clean tick advances the heartbeat.
        """

        require_autocommit()
        with recovery_tick(self.work_name) as recovery:
            results = self._run_steps((*self.derivation_steps(), *self.recorded_work_steps()), recovery)
        return results.get(RecoveryPhase.DISPATCH, 0)

    @final
    def derive(self) -> int:
        """Run only the derivation phases of a tick, then raise every failure they isolated."""
        with recovery_tick(self.work_name, heartbeat=False) as recovery:
            results = self._run_steps(self.derivation_steps(), recovery)
        return sum(results.values())

    @staticmethod
    def _run_steps(steps: tuple[RecoveryStep, ...], recovery: RecoveryPass) -> dict[RecoveryPhase, int]:
        phases = [step.phase for step in steps]
        if len(set(phases)) != len(phases):
            # Metrics, holds and failure notes are keyed by phase; two loops under one name are indistinguishable.
            raise ValueError(f"a recovery tick declares a phase twice: {[phase.value for phase in phases]}")
        results: dict[RecoveryPhase, int] = {}
        for step in steps:
            # A whole phase that raises (a broken candidate query, a lost
            # database connection) is isolated like one identity: later phases run.
            with recovery.isolate(step.phase):
                results[step.phase] = results.get(step.phase, 0) + step.run(recovery)
        return results

    def derivation_steps(self) -> tuple[RecoveryStep, ...]:
        """
        Phases that record or settle obligations from product facts and provider evidence.

        Capabilities extend this tuple, never ``recover``/``derive``. A step that
        loops over identities uses ``recovery.run_page`` (or the existing lease
        reservation protocol), so failed rows cannot monopolize its first page.
        ``isolate`` alone catches errors but does not skip deferred candidates.
        Bind each custom callback in ``EffectExamples.recovery_steps``; naming
        a phase does not generate its product fixtures or fault seam.
        """
        return ()

    def recorded_work_steps(self) -> tuple[RecoveryStep, ...]:
        """Phases over work rows that are already recorded; they never read product facts."""
        steps = (
            RecoveryStep(RecoveryPhase.REAP, self._reap),
            RecoveryStep(RecoveryPhase.DISPATCH, self._dispatch),
            RecoveryStep(RecoveryPhase.CENSUS, self._census),
            RecoveryStep(RecoveryPhase.RETENTION, self._retain),
        )
        if self.deadline_wakeup_horizon is not None:
            steps += (RecoveryStep(RecoveryPhase.DEADLINE_WAKEUP, self._wake_near_deadlines),)
        return steps

    def _reap(self, recovery: RecoveryPass) -> int:
        # The tick's clock, read once (its holds use the same instant), not a
        # read of its own. A lease that expires after that instant is reaped
        # next tick. Safe for dispatch below: an expired reservation becomes
        # due at this instant, which dispatch's later clock still covers.
        return reap_expired_work(
            self.model,
            max_rearms=self.policy.max_rearms,
            retry_delay=self.policy.delay_at(0),
            batch_size=self.policy.batch_size,
            finalize_unknown=self._finalize_unknown,
            finalize_retry=self._finalize_retry,
            retry_delay_for=self.retry_delay_for,
            settle_expired_claim=self.settle_expired_claim,
            now=recovery.clock,
            recovery=recovery,
        )

    def _dispatch(self, recovery: RecoveryPass) -> int:
        # INVARIANT: dispatch reads a fresh clock, never ``recovery.clock``.
        # Earlier phases of this tick make work due after the tick's clock was
        # read: discovered work, a reconciled non-application and a reopened
        # stale write take ``available_at`` from their own later reads.
        # Selecting at the tick's instant would leave that work for the next
        # tick, one whole cadence late. A phase that selects rows another phase
        # can make due mid-tick needs a clock read taken after those phases ran.
        # Pinned by test_recover_dispatch_clock.py.
        return dispatch_due_work(
            self.model,
            publish=self.nudge,
            eligibility=self.eligibility,
            lease_duration=self.policy.lease_duration,
            batch_size=self.policy.batch_size,
            recovery=recovery,
        )

    def _census(self, recovery: RecoveryPass) -> int:
        undiscovered = 0
        # The application's candidate query must not take the recorded-work gauges down with it.
        with recovery.isolate(RecoveryPhase.DISCOVERY):
            undiscovered = self.undiscovered_count(now=recovery.clock)
        # Gauges, not selections: judging ages at the tick's clock reads them
        # low by at most the tick's own duration. The tick itself publishes its
        # holds after the last phase (``recovery_tick``).
        publish_work_census(self.model, undiscovered=undiscovered, eligibility=self.eligibility, now=recovery.clock)
        return 0

    def _retain(self, recovery: RecoveryPass) -> int:
        if self.policy.retention is None:
            return 0
        # A cut-off at the tick's clock keeps a success at most one tick's
        # duration longer than ``retention``; nothing else reads it.
        return prune_completed_work(
            self.model,
            completed_before=recovery.clock() - self.policy.retention,
            batch_size=self.policy.batch_size,
        )

    def undiscovered_count(self, *, now: NowFn = database_now) -> int:
        """Canonical obligations not yet materialized at ``now``; zero for atomic requirements."""
        return 0


def _wake(model: type[UniverseDurableWork], work_id: int, enqueue: Callable[[int], object]) -> WakeAction:
    action = wake_work(model, work_id)
    if action is not WakeAction.QUEUED:
        transport.notify_after_commit(model._meta.label_lower, enqueue, work_id)
    return action


def _enqueue_registered(model: type[UniverseDurableWork], work_id: int) -> None:
    # Lazy import, as in registered_nudge: the registry and task modules import this one.
    from due.django.runtime.registry import registration_for

    registration_for(model).capability.enqueue_execution(work_id)


def registered_nudge(model: type[UniverseDurableWork]) -> Publisher:
    """Build the standard low-latency wake-up for a registered capability."""

    def nudge(work_id: int, token: UUID) -> object:
        # Lazy import: the registry and task modules import this one.
        from due.django.runtime.registry import registration_for
        from due.django.runtime.tasks import run_durable_work_task

        registration = registration_for(model)
        return run_durable_work_task.apply_async(
            args=(registration.name, work_id, str(token)),
            queue=registration.queue,
            time_limit=registration.execution_time_limit,
            soft_time_limit=registration.execution_soft_time_limit,
        )

    return nudge


@dataclass
class _DurableWorkTransaction[
    OwnerT: models.Model,
    WorkT: UniverseDurableWork,
    ResultT = None,
]:
    """Immediate database changes plus one deferred worker operation, atomically."""

    _capability: OwnedDurableWorkCapability[OwnerT, WorkT, ResultT]
    locked_owner: OwnerT
    existing_work: WorkT | None
    _open: bool = True
    _scheduled: bool = False
    _notify_worker: bool = True

    def request_progress(self) -> bool:
        """
        Nudge existing active intent after commit, preserving revision and backoff.

        Call after changing an eligibility prerequisite under this owner lock.
        Missing or stopped work is not admitted/rearmed; recovery remains the
        durable fallback if the notification is lost.
        """
        if not self._open or self._scheduled:
            raise RuntimeError("request_progress() must be the single operation inside atomic()")
        current = self.existing_work
        if current is None or current.state not in ACTIVE_WORK_STATES:
            return False
        self._scheduled = True
        if self._notify_worker:
            transport.notify_after_commit(self._capability.work_name, self._capability.enqueue_execution, current.pk)
        return True

    def retry_failed(self) -> bool:
        """
        Explicitly retry unchanged replay-safe input after a stopped failure.

        Call inside atomic() after application authorization. Ordinary repeated
        requirements preserve their outcome. UNKNOWN and provider occurrences
        require evidence reconciliation and cannot use this shortcut.
        """
        if not self._open or self._scheduled:
            raise RuntimeError("retry_failed() must be the single operation inside atomic()")
        if self._capability.model.requires_effect_evidence:
            raise ValueError("provider occurrences require evidence reconciliation")
        current = self.existing_work
        if current is None or current.state not in (
            DurableWorkState.EXHAUSTED,
            DurableWorkState.TERMINAL_FAILED,
        ):
            return False
        assert supersede_work(self._capability.model, current.pk)
        current.refresh_from_db()
        self.request_progress()
        return True

    def capture_work(self, **values: object) -> WorkT:
        """Construct canonical work from this transaction's fresh locked owner."""
        if not self._open:
            raise RuntimeError("capture_work() must be called inside atomic()")
        capture = self._capability.capture
        if capture is None:
            raise TypeError("declare capture or pass an explicit work model")
        fields = self._capability.domain_fields
        allowed = {name for field in fields for name in (field.name, field.attname)}
        if values.keys() - allowed:
            raise ValueError("captured overrides must name domain fields")
        # Field names and FK attnames designate the same destination. An
        # explicit event=None must also suppress a captured event_id path.
        overrides = dict(values)
        for field in fields:
            if field.name != field.attname:
                if field.name in values and field.attname in values:
                    raise ValueError("pass only one spelling of a relation field")
                if field.name in values:
                    overrides[field.attname] = values[field.name]
                elif field.attname in values:
                    overrides[field.name] = values[field.attname]
        captured = capture.read(self.locked_owner, **overrides)
        desired: dict[str, object] = {}
        for field in fields:
            if field.name in values:
                desired[field.name] = values[field.name]
            elif field.attname in values:
                desired[field.attname] = values[field.attname]
            elif field.name in captured:
                desired[field.attname] = captured[field.name]
            elif field.attname in captured:
                desired[field.attname] = captured[field.attname]
        return self._capability.model(**desired)

    def request_work(self, work: WorkT | None = None, *, not_before: datetime | None = None, **values: object) -> WorkT:
        """
        Record intent now; notify the worker only after the outermost commit.

        This never calls the provider or mutates the product owner. Unchanged
        input preserves completion, retry budget and a stopped failure; new input
        starts a revision. Re-arming stopped work is a separate authorized
        decision: retry_failed(). Recovery owns delivery if the post-commit
        notification is lost.
        """

        if not self._open:
            raise RuntimeError("request_work() must be called inside atomic()")
        if self._scheduled:
            raise RuntimeError("request_work() may be called only once per atomic()")
        if not_before is not None and is_naive(not_before):
            raise ValueError("not_before must be timezone-aware")

        if work is None:
            work = self.capture_work(**values)
        elif values:
            raise TypeError("pass either an explicit work model or captured field overrides")
        desired = self._capability._desired_work(
            owner=self.locked_owner,
            work=work,
        )
        current = self.existing_work
        if current is None:
            # Enrollment and claiming share the coordination clock. The model
            # default remains a safeguard for direct ORM inserts, not a second
            # clock source for library-authored work.
            desired.available_at = not_before if not_before is not None else database_now()
            desired.save(force_insert=True)
            current = desired
        elif not self._capability._same_domain_state(current, desired):
            if self._capability.model.requires_effect_evidence:
                raise ValueError(
                    "a provider operation preserves one occurrence; create another owner for another operation"
                )
            update_fields: list[str] = []
            for model_field in self._capability.domain_fields:
                setattr(
                    current,
                    model_field.attname,
                    model_field.value_from_object(desired),
                )
                update_fields.append(model_field.name)
            current.save(update_fields=[*update_fields, "updated_at"])
            assert current.id is not None, "persisted durable effect must have an id"
            # INVARIANT: desired-state mutation and revision invalidation are
            # one transaction, so an old claim can never settle the new work.
            assert supersede_work(self._capability.model, current.id)
            if not_before is not None:
                self._capability.model.objects.filter(pk=current.id).update(available_at=not_before)
            current.refresh_from_db()

        assert current.id is not None, "persisted durable effect must have an id"
        self.existing_work = current
        self.request_progress()
        self._scheduled = True  # Recording stopped intent still consumes this transaction's one operation.
        return current


@dataclass(frozen=True, slots=True)
class OwnedDurableWorkCapability[
    OwnerT: models.Model,
    WorkT: UniverseDurableWork,
    ResultT = None,
](DurableWorkCapability[WorkT, ResultT]):
    """
    Owner-bound scheduling porcelain plus the standard recovery API.

    Domain services name their immediate product mutations explicitly. Inside
    ``atomic(owner_id)``, they change the product row and ``request_work``
    records the deferred effect. Only ``apply(...)`` performs provider I/O.
    Owner/work locking, construction, revision supersession, post-commit worker
    notification and ``recover()`` are inherited.
    """

    owner_model: type[OwnerT]
    owner_field_name: str
    discovery: Discovery[OwnerT, WorkT] | None = None
    capture: Capture | None = None
    input_fields: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        owner_field = self.model._meta.get_field(self.owner_field_name)
        if self.model.work_owner_field != self.owner_field_name:
            raise TypeError("work.work_owner_field must match the declared owner for consistent lock ordering")
        if not isinstance(owner_field, models.OneToOneField):
            raise TypeError(f"{self.model.__name__}.{self.owner_field_name} must be a OneToOneField")
        if owner_field.remote_field.model is not self.owner_model:
            raise TypeError(
                f"{self.model.__name__}.{self.owner_field_name} points to "
                f"{owner_field.remote_field.model}, not {self.owner_model.__name__}"
            )
        if self.input_fields is not None:
            available = {field.name for field in self._application_fields}
            if len(set(self.input_fields)) != len(self.input_fields):
                raise ValueError("input_fields must not contain duplicates")
            if unknown := set(self.input_fields) - available:
                raise ValueError(f"input_fields must name application fields: {sorted(unknown)}")
            if "universe" not in self.input_fields:
                raise ValueError("input_fields must include universe")
        if self.capture is not None:
            for field in self.domain_fields:
                if (
                    field.name != field.attname
                    and field.name in self.capture.paths
                    and field.attname in self.capture.paths
                ):
                    raise ValueError("declare only one spelling of a relation field")
            self.capture.validate(
                self.owner_model, {name for field in self.domain_fields for name in (field.name, field.attname)}
            )
        if not self.domain_fields:
            raise TypeError(f"{self.model.__name__} must declare desired-state fields")
        if self.discovery is not None and (problem := deferral_identity_problem(self.owner_model._meta.pk)):
            # Discovery holds a poisoned owner out by its primary key (RecoveryDeferral.identity).
            raise TypeError(f"{self.owner_model.__name__} cannot be a discovery owner: {problem}")
        if (
            self.discovery is not None
            and self.discovery.applied_evidence is AppliedEvidenceContract.RETAINED_WORK_ROW
            and self.policy.retention is not None
        ):
            raise ValueError("discovered work using retained work-row evidence must disable retention")

    @classmethod
    def for_owner(
        cls,
        *,
        work: type[WorkT],
        owner: type[OwnerT],
        nudge: Publisher | None = None,
        policy: DurableWorkPolicy,
        discovery: Discovery[OwnerT, WorkT] | None = None,
        capture: Capture | None = None,
        input_fields: tuple[str, ...] | None = None,
        eligibility: ExecutionEligibility | None = None,
        wakes: tuple[Dependent[WorkT], ...] = (),
    ) -> Self:
        """Bind one typed work model to its canonical aggregate owner."""

        owner_field = work.work_owner_field
        if owner_field is None:
            raise TypeError("owned work must declare work_owner_field on its model")
        return cls(
            model=work,
            nudge=nudge if nudge is not None else registered_nudge(work),
            policy=policy,
            discovery=discovery,
            capture=capture,
            input_fields=input_fields,
            eligibility=eligibility,
            wakes=wakes,
            owner_model=owner,
            owner_field_name=owner_field,
        )

    def derivation_steps(self) -> tuple[RecoveryStep, ...]:
        if self.discovery is None:
            return super().derivation_steps()
        return (*super().derivation_steps(), RecoveryStep(RecoveryPhase.DISCOVERY, self._discover))

    def _discover(self, recovery: RecoveryPass) -> int:
        """Bound and re-evaluate one discovery page."""
        require_autocommit()
        discovery = self.discovery
        assert discovery is not None, "discovery steps exist only for a declared discovery"
        # Owners this phase isolated recently are held out with backoff, so a
        # poisoned owner stops leading every page; they are still candidates
        # (undiscovered_count counts them) and are retried when their hold ends.
        return recovery.run_page(
            RecoveryPhase.DISCOVERY,
            discovery.bounded_candidates(now=recovery.clock()).values_list("pk", flat=True),
            discovery.page_size,
            lambda owner_id: self._derive_owner(discovery, owner_id),
        )

    def _derive_owner(self, discovery: Discovery[OwnerT, WorkT], owner_id: int | str | UUID) -> bool:
        try:
            with self.atomic(owner_id, notify_worker=False) as effect:
                desired = discovery.desired_for(effect.locked_owner, effect.existing_work)
                if desired is None:
                    return False
                effect.request_work(desired)
                return True
        except self.owner_model.DoesNotExist:
            # Candidate pages race product deletion. A later page can only
            # rediscover extant owners, so a vanished candidate is settled.
            if self.owner_model.objects.filter(pk=owner_id).exists():
                raise
            return False

    def undiscovered_count(self, *, now: NowFn = database_now) -> int:
        if self.discovery is None:
            return 0
        return self.discovery.bounded_candidates(now=now()).count()

    @property
    def owner_attname(self) -> str:
        owner_field = self.model._meta.get_field(self.owner_field_name)
        assert isinstance(owner_field, models.OneToOneField)
        return owner_field.attname

    @property
    def coordination_field_names(self) -> frozenset[str]:
        return frozenset(model_field.name for model_field in DurableWork._meta.concrete_fields)

    @property
    def _application_fields(self) -> tuple[models.Field[Any, Any], ...]:
        coordination_field_names = self.coordination_field_names
        return tuple(
            model_field
            for model_field in self.model._meta.concrete_fields
            if not model_field.primary_key
            and model_field.name not in coordination_field_names
            and model_field.name != self.owner_field_name
        )

    @property
    def domain_fields(self) -> tuple[models.Field[Any, Any], ...]:
        """
        Canonical inputs for capture, construction and revision comparison.

        Omitted application fields are persisted progress. Requests preserve
        them on existing rows and cannot initialize them. Rich capabilities
        without an explicit declaration retain their historical inference.
        """
        if self.input_fields is None:
            return self._application_fields
        return tuple(field for field in self._application_fields if field.name in self.input_fields)

    @contextmanager
    def atomic(
        self,
        owner_id: int | str | UUID,
        *,
        notify_worker: bool = True,
        wait_for_owner: bool = True,
    ) -> Iterator[_DurableWorkTransaction[OwnerT, WorkT, ResultT]]:
        """
        Lock owner then work; provider execution is deferred.

        wait_for_owner=False never waits on either lock: a busy owner or work
        row raises WorkOwnerBusy. This also supports child admission under an aggregate lock when
        the child was just INSERTed in this transaction (no other writer can
        hold it). Never SKIP a busy work row and mistake it for missing intent.
        """

        with transaction.atomic():
            try:
                owner = self.owner_model.objects.select_for_update(skip_locked=not wait_for_owner).get(pk=owner_id)
            except self.owner_model.DoesNotExist:
                if not wait_for_owner and self.owner_model.objects.filter(pk=owner_id).exists():
                    raise WorkOwnerBusy(f"{self.owner_model._meta.label_lower}:{owner_id}") from None
                raise
            try:
                current = (
                    self.model.objects.select_for_update(nowait=not wait_for_owner)
                    .filter(**{self.owner_attname: owner_id})
                    .first()
                )
            except OperationalError as error:
                # One busy contract for both locks: existing callers (e.g.
                # MediaLive retirement) must not receive a new database error.
                if not wait_for_owner and isinstance(error.__cause__, LockNotAvailable):
                    raise WorkOwnerBusy(f"{self.model._meta.label_lower}:owner={owner_id}") from error
                raise

            scheduled = _DurableWorkTransaction(
                _capability=self,
                locked_owner=owner,
                existing_work=current,
                _notify_worker=notify_worker,
            )
            try:
                yield scheduled
            finally:
                scheduled._open = False

    def _desired_work(
        self,
        *,
        owner: OwnerT,
        work: WorkT,
    ) -> WorkT:
        if type(work) is not self.model or work.pk is not None or not work._state.adding:
            raise TypeError(f"request_work requires a new {self.model.__name__} input")
        for model_field in DurableWork._meta.concrete_fields:
            if model_field.primary_key or model_field.name in {"fencing_token", "available_at"}:
                continue  # Neither incoming identity nor availability is ever copied.
            if model_field.value_from_object(work) != model_field.get_default():
                raise TypeError(f"coordination field {model_field.name} is kernel-owned")
        if self.input_fields is not None:
            for field in self._application_fields:
                if field.name not in self.input_fields and field.value_from_object(work) != field.get_default():
                    raise TypeError(f"progress field {field.name} cannot be supplied as desired input")
        owner_id = getattr(work, self.owner_attname)
        if owner_id is not None and owner_id != owner.pk:
            raise TypeError("work belongs to a different owner than atomic()")
        desired = self.model(
            **{
                self.owner_field_name: owner,
                **{field.attname: field.value_from_object(work) for field in self.domain_fields},
            }
        )
        desired.full_clean(
            exclude=[*self.coordination_field_names, self.owner_field_name],
            validate_unique=False,
            validate_constraints=False,
        )
        return desired

    def _same_domain_state(self, current: WorkT, desired: WorkT) -> bool:
        return all(
            model_field.value_from_object(current) == model_field.value_from_object(desired)
            for model_field in self.domain_fields
        )


class ReplaySafeEffect[OwnerT: models.Model, WorkT: UniverseDurableWork, ResultT = None](
    OwnedDurableWorkCapability[OwnerT, WorkT, ResultT]
):
    """
    An ordinary replay-safe provider method under the shared execution guard.

    Use ``OwnedDurableWorkCapability.apply`` directly when a workflow needs
    separate guarded calls or independent compensation. This convenience does
    not add provider evidence or make a non-idempotent operation replay-safe.
    """

    @abstractmethod
    def perform_effect(self, work: WorkT) -> ResultT | AppliedEffectEvidence[ResultT] | ContinueAfter:
        """Perform replay-safe I/O; return a result or the next observation delay."""

    def apply(self, work: WorkT, context: WorkExecutionContext[WorkT]) -> ResultT | ContinueAfter:
        with context.external_call():
            return effect_result(self.perform_effect(work))


class DatabaseEffect[OwnerT: models.Model, WorkT: UniverseDurableWork, ResultT = None](
    OwnedDurableWorkCapability[OwnerT, WorkT, ResultT]
):
    """One short PostgreSQL transition, committed with its continuation or result.

    Return ContinueAfter to release the worker until the next transition. The
    runtime owns claim fencing, locking, rollback and recovery. All writes must
    use Due's database connection; provider calls belong in ReplaySafeEffect or
    an evidence-aware capability. No checkpoint table or workflow history is added.
    """

    _database_only: ClassVar[bool] = True

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_simple_callbacks(self, DatabaseEffect)

    @abstractmethod
    def transition(self, work: WorkT) -> ResultT | ContinueAfter:
        """Change product rows inside this work's fenced transaction."""

    @final
    def apply(self, work: WorkT, context: WorkExecutionContext[WorkT]) -> ResultT | ContinueAfter:
        require_fenced_settlement(work)
        return self.transition(work)


def _validate_simple_callbacks(
    capability: OwnedDurableWorkCapability[Any, Any, Any],
    mode: type[OwnedDurableWorkCapability[Any, Any, Any]],
) -> None:
    """Reject accidental use of rich hooks on a product-callback capability."""
    for method in ("apply", "finalize_success", "_finalize_success", "on_success"):
        if getattr(type(capability), method) is not getattr(mode, method):
            raise TypeError(f"{method} is runtime-owned on {mode.__name__}; use its product callback or the rich mode")
    if capability.input_fields is None:
        raise TypeError(f"{mode.__name__} requires explicit input_fields")
