"""
Bindings for the shared runtime; domains still prove their real side effect.

This is not a reference implementation. Every semantic callback invokes the
same production coordinator as the registered worker. A domain supplies only
its genuine requirement factory and an external replay/failure seam.
"""

from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from celery import Task
from django.db.models import F
from prometheus_client import REGISTRY
from pytest_obligation.integrations.django.lifecycle_states import Lifecycle
from pytest_obligation.profiles.automatic_recovery import (
    CONTRACT_PAGE_SIZE,
    DueWorkSweep,
    InFlightExecution,
    OwedWorkVariant,
    PoisonSwitch,
)
from pytest_obligation.profiles.bounded_ownership import FencedOwnership
from pytest_obligation.profiles.durable_retention import Retention

from due.django.identities import work_id_from_wire
from due.django.models import DurableWorkState
from due.django.public import (
    DurableWorkCapability,
    UniverseDurableWork,
    WorkClaim,
    claim_work,
    complete_work,
    database_now,
    prune_completed_work,
    reap_expired_work,
    renew_work,
    retry_work,
    terminal_work,
)
from due.django.runtime import coordination
from due.django.runtime.names import METRIC_NAMESPACE
from due.django.runtime.registry import DurableWorkRegistration
from due.django.testing import end_recovery_holds, expire_lease, hold_nudges, make_due


def assert_registered_worker_options(entry: DurableWorkRegistration, options: Mapping[str, object]) -> None:
    """Observe broker options; executing the task inline cannot enforce Celery limits."""
    for option, expected in (
        ("queue", entry.queue),
        ("time_limit", entry.execution_time_limit),
        ("soft_time_limit", entry.execution_soft_time_limit),
    ):
        assert options.get(option) == expected, f"{entry.name}: delivered worker {option} drifted"


@dataclass(frozen=True)
class DurableWorkBindings[W: UniverseDurableWork, ResultT = None]:
    """Advanced adapter from a registered capability to shared profile contracts.

    ``capability`` owns all runtime transitions; ``make`` admits fresh work through
    production. Observation, lease aging and task delivery helpers expose those
    transitions to shared proofs. Prefer effect_contract_suite for application
    enrollment, which also enforces mode-specific examples and report completeness."""

    capability: DurableWorkCapability[W, ResultT]
    make_work: Callable[[], W]
    identity_of: Callable[[W], Any] = lambda work: work.pk
    #: Application-effect entries per work id, recorded at the kernel's one
    #: handler call site: the effect cannot run without an admitted claim.
    effect_entries: Counter[int] = field(default_factory=Counter, compare=False)

    @property
    def registration(self):
        from due.django.conformance.registrations import (
            registered_capability,
            registration_of,
        )

        entry = registration_of(self.capability.model)
        if registered_capability(entry.name) is not self.capability:
            raise ValueError(f"{entry.name}: bind the registered production capability, not a replacement")
        return entry

    def assert_scheduled(self) -> None:
        from due.django.conformance.suite import assert_registered_schedule

        assert_registered_schedule(self.registration)

    def make(self, *, age: timedelta = timedelta(minutes=1)) -> W:
        # ARRANGE: real requirement, with only timestamps aged to simulate time.
        with hold_nudges():
            work = self.make_work()
        self.capability.model.objects.filter(pk=work.pk).update(
            created_at=database_now() - age,
            available_at=database_now() - age,
        )
        work.refresh_from_db()
        return work

    def age(self, work: W) -> None:
        make_due(work)

    def age_by(self, work: W, delta: timedelta) -> None:
        """Let part of a retry wait elapse without executing or settling work."""
        self.capability.model.objects.filter(pk=work.pk).update(available_at=F("available_at") - delta)
        work.refresh_from_db()

    def observe(self, work: W) -> tuple:
        work.refresh_from_db()
        return work.state, work.fencing_token, work.desired_revision, work.lease_expires_at, work.rearm_count

    def claim(self, work: W) -> WorkClaim[W]:
        """Arrange an admitted production claim; a refused fixture claim is a broken precondition."""
        claim = claim_work(self.capability.model, work.pk, lease_duration=self.capability.policy.lease_duration)
        assert claim is not None, f"{self.capability.work_name}: arranged work {work.pk} was not claimable"
        return claim

    def retry(self, work: W) -> None:
        """Arrange one real failure using this capability's admission and retry policy."""
        claim = self.claim(work)
        retry_work(
            claim,
            error_code="injected",
            max_rearms=self.capability.policy.max_rearms,
            retry_delay=self.capability.retry_delay_for(claim.work),
        )

    def retryable(self, *, age: timedelta) -> W:
        work = self.make(age=age)
        self.retry(work)
        self.age(work)
        return work

    def terminal(self, *, age: timedelta) -> list[W]:
        result = []
        for exhaust in (False, True) if self.capability.policy.max_rearms is not None else (False,):
            work = self.make(age=age)
            if exhaust:
                assert self.capability.policy.max_rearms is not None
                for attempt in range(self.capability.policy.max_rearms + 1):
                    if attempt:
                        self.age(work)
                    self.retry(work)
            else:
                complete_work(self.claim(work))
            self.capability.model.objects.filter(pk=work.pk).update(completed_at=database_now() - age)
            result.append(work)
        # A definite permanent failure, through the same fenced transition the executor uses.
        work = self.make(age=age)
        terminal_work(self.claim(work), error_code="injected")
        result.append(work)
        return result

    def execute(self, identity: Any) -> bool:
        # REAL PRODUCTION: the registered executor's synchronous admission path.
        matches = [w for w in self.capability.model.objects.all() if self.identity_of(w) == identity]
        assert len(matches) == 1, f"{len(matches)} {self.capability.model.__name__} rows have identity {identity!r}"
        (work,) = matches
        original = coordination._execute_claim

        def counted(claim: Any, **options: Any) -> bool:
            # OBSERVE — the handler is about to run for this identity; delegate unchanged.
            self.effect_entries[claim.work_id] += 1
            return original(claim, **options)

        with patch.object(coordination, "_execute_claim", counted):
            return self.capability.execute_now(work)

    def lifecycle(self) -> Lifecycle:
        policy = self.capability.policy
        excluded = {
            **self.capability.model.work_external_states,
            DurableWorkState.QUEUED.value: (
                "a lost reservation returns to READY in recover() before selection; owned by the generated "
                "test_registered_producer_lost_notification and test_lost_delivery_spends_no_retry_budget"
            ),
            DurableWorkState.CLAIMED.value: (
                "an expired claim is reaped before selection; owned by the generated "
                "test_due_work_contract[B-assert_expired_lease_permits_reclaim]"
            ),
            DurableWorkState.UNKNOWN.value: (
                "evidence-owned debt that is never redispatched blindly; owned by the generated "
                "test_uncertainty_blocks_resend and test_receipt_repairs_product_without_repeating_effect"
            ),
        }
        if policy.max_rearms is None:
            excluded[DurableWorkState.EXHAUSTED.value] = (
                "max_rearms=None never exhausts a failure; only an expired ContinueAfter(until=) deadline "
                "does, owned by durable_work/tests/test_continuations.py::"
                "test_expired_continuation_deadline_retains_exhaustion_evidence"
            )
        if policy.max_rearms == 0:
            excluded[DurableWorkState.RETRYABLE.value] = (
                "max_rearms=0: the first failure exhausts, so nothing is retryable"
            )
        return Lifecycle(
            state_fields=("state",),
            execute=self.execute,
            excluded_states=excluded,
            effect_calls_for=lambda work: self.effect_entries[work.pk],
        )

    @contextmanager
    def sweep(self) -> Iterator[DueWorkSweep]:
        # ARRANGE: domain factories create genuine obligations.
        # INVARIANT: the tick is the registered capability's own recover() over
        # the same model, nudge and coordinator; only the declared page shrinks.
        # A tick that ignored policy.batch_size would still over-dispatch here.
        capability = replace(
            self.capability,
            policy=replace(
                self.capability.policy, batch_size=min(self.capability.policy.batch_size, CONTRACT_PAGE_SIZE)
            ),
        )
        counts = Counter()
        dispatched = []
        deliveries = []
        from due.django.runtime.tasks import run_durable_work_task

        registration_name = self.registration.name
        refusals = PoisonSwitch()

        def publish(*, args, **options):
            # The task is shared by every capability. Other model hooks may
            # publish alongside this proof; leave their transport unchanged.
            if args[0] != registration_name:
                return Task.apply_async(run_durable_work_task, args=args, **options)
            # EXTERNAL SEAM: reserve in production, hold only the broker boundary.
            refusals.check(work_id_from_wire(self.capability.model, args[1]), f"broker refused work {args[1]}")
            deliveries.append(options)
            counts[work_id_from_wire(self.capability.model, args[1])] += 1
            dispatched.append(self.identity_of(self.capability.model.objects.get(pk=args[1])))

        def run_tick():
            result = capability.recover()
            # Assert outside recovery's intentional broker-error isolation.
            for options in deliveries:
                assert_registered_worker_options(self.registration, options)
            return result

        @contextmanager
        def start(work, run_tick):
            run_tick()
            yield

        def poison(work):
            # EXTERNAL SEAM: the broker refuses this one work id on every publication.
            return refusals.poisoned(work.pk)

        def resume_recovery():
            end_recovery_holds(self.capability.model)
            # Only time passes: production must reclaim and redispatch every
            # lost reservation; never reset its state from the binding.
            self.capability.model.objects.filter(state=DurableWorkState.QUEUED).update(
                lease_expires_at=database_now() - timedelta(seconds=1)
            )

        with patch("due.django.runtime.tasks.run_durable_work_task.apply_async", publish):
            yield DueWorkSweep(
                # REAL PRODUCTION: exact coordinator selection, tick and transitions.
                name=self.capability.model._meta.label_lower,
                due_work=lambda: self.capability.due_work(),
                run_tick=run_tick,
                make_owed=self.make,
                make_terminal=self.terminal,
                recovery_delay=timedelta(0),
                page_size=capability.policy.dispatch_limit,
                assert_scheduled=self.assert_scheduled,
                tick_interval=timedelta(seconds=self.registration.cadence_seconds),
                in_flight=InFlightExecution(
                    make_owed=self.make,
                    start=start,
                    execution_count_for=lambda work: counts[work.pk],
                ),
                # OBSERVE: production metric and actual published identities.
                observe_outstanding=lambda: REGISTRY.get_sample_value(
                    f"{METRIC_NAMESPACE}_outstanding",
                    {"effect": self.capability.model._meta.label_lower},
                ),
                dispatched_ids=lambda: dispatched,
                identity_of=self.identity_of,
                additional_owed_variants=(OwedWorkVariant(name="retryable", make=self.retryable),)
                if self.capability.policy.max_rearms != 0
                else (),
                lifecycle=self.lifecycle(),
                poison=poison,
                resume_recovery=resume_recovery,
                still_owed=lambda work: (
                    coordination.outstanding_work(self.capability.model).filter(pk=work.pk).exists()
                ),
            )

    def ownership(self) -> FencedOwnership:
        # ARRANGE: persisted work; backdate only the lease to simulate worker loss.
        # EXTERNAL SEAM: none; all transitions use the real database coordinator.
        claims = {}
        ids = []
        model, policy = self.capability.model, self.capability.policy

        def make():
            ids.append(self.make().pk)
            return ids[-1]

        def claim():
            value = claim_work(model, ids[-1], lease_duration=policy.lease_duration)
            if value is None:
                return None
            claims[value.token] = value
            return value.work_id, value.token

        def expire(pk) -> None:
            expire_lease(model.objects.get(pk=pk))

        def reclaim(pk):
            result = reap_expired_work(
                model,
                max_rearms=policy.max_rearms,
                retry_delay=policy.delay_at(0),
                batch_size=policy.batch_size,
                finalize_unknown=self.capability.finalize_unknown,
                finalize_retry=self.capability.finalize_retry if model.requires_effect_evidence else None,
                retry_delay_for=self.capability.retry_delay_for,
            )
            # ARRANGE: advance the real retry delay after the real reclaim transition.
            self.age(model.objects.get(pk=pk))
            return result

        return FencedOwnership(
            # REAL PRODUCTION: transitions identical to the registered executor.
            name=model._meta.label_lower,
            make_claimable=make,
            claim=claim,
            fenced_write=lambda pk, token: complete_work(claims[token]),
            # Every other owner write the executor makes (coordination's settle paths):
            # a stale worker's retry, permanent failure, unresolved outcome or
            # continuation must be refused as its completion is.
            other_fenced_writes={
                "retry": lambda pk, token: retry_work(
                    claims[token],
                    error_code="injected",
                    max_rearms=policy.max_rearms,
                    retry_delay=self.capability.retry_delay_for(claims[token].work),
                ),
                "terminal": lambda pk, token: terminal_work(claims[token], error_code="injected"),
                "unknown": lambda pk, token: coordination.unknown_work(claims[token], error_code="injected"),
                "continue": lambda pk, token: coordination.continue_work(
                    claims[token], coordination.ContinueAfter(delay=timedelta(minutes=1), until=None)
                ),
            },
            renew_lease=lambda pk, token: renew_work(claims[token], lease_duration=policy.lease_duration),
            expire_lease=expire,
            reclaim_stalled=reclaim,
            no_disposition=0,
            # OBSERVE: actual persisted lifecycle state.
            observe=lambda pk: self.observe(model.objects.get(pk=pk)),
        )

    def retention(self) -> Retention:
        # ARRANGE: age actual owed and completed work.
        # EXTERNAL SEAM: none.
        retention = self.capability.policy.retention
        assert retention is not None
        return Retention(
            # REAL PRODUCTION: invoke the same pruning pass as recovery.
            name=self.capability.model._meta.label_lower,
            make_non_terminal=lambda: self.make(age=retention + timedelta(days=1)),
            make_prunable=lambda: self.terminal(age=retention + timedelta(days=1))[0],
            run_retention=lambda: prune_completed_work(
                self.capability.model,
                completed_before=database_now() - retention,
            ),
            # OBSERVE: actual existence after production pruning.
            still_exists=lambda work: self.capability.model.objects.filter(pk=work.pk).exists(),
        )
