"""Primitive-owned enrollment and bindings; independent harness-owned assertions."""

from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from functools import wraps
from typing import Any
from unittest.mock import patch

import pytest
from asgiref.sync import AsyncSingleThreadContext
from django.conf import settings
from django.db import connection, models
from pytest_obligation.binding import is_real_reason
from pytest_obligation.contract import (
    Claim,
    Decline,
    DueWorkSource,
    NotApplicable,
    NotAssessed,
    ObligationContract,
    ObligationContractDesignError,
    Profile,
    due_work_contract_suite,
)
from pytest_obligation.crash_histories import HandoffHistory
from pytest_obligation.evidence.observation import assert_observation
from pytest_obligation.integrations.django.edge_values import CandidateEdge
from pytest_obligation.integrations.django.lock_order import recording_against_the_ledger
from pytest_obligation.integrations.django.writes import (
    execute_reporting_autocommit_write,
    row_write,
)
from pytest_obligation.interleavings.bindings import Scenario
from pytest_obligation.process_histories import ProcessHistory
from pytest_obligation.profiles.catalog import ConvergenceFamily
from pytest_obligation.profiles.crash_ambiguity import AmbiguityAware
from pytest_obligation.profiles.durable_retention import Retention
from pytest_obligation.profiles.eventual_convergence import (
    ConvergentWrite,
    SupersededSnapshot,
)
from pytest_obligation.profiles.fact_derived_obligations import StateDerived
from pytest_obligation.profiles.gated_execution import (
    ExecutionGate,
    ExecutionGateBinding,
)
from pytest_obligation.profiles.harmless_replay import ReplaySafeEffect
from pytest_obligation.profiles.indivisible_admission import AdmissionAtomicity
from pytest_obligation.profiles.job_retry_limits import BoundedRetry

from due.django.capabilities.authoring import FAILED_TERMINAL_STATES
from due.django.capabilities.authoring import ReplaySafeEffect as ReplaySafeCapability
from due.django.capabilities.compensation import IndependentCompensation
from due.django.capabilities.observed_operations import ObservedExternalOperation
from due.django.capabilities.operations import DurableExternalOperation
from due.django.capabilities.projection import DurableProjection
from due.django.capabilities.scheduled import DurableScheduledAction
from due.django.conformance._binding import Binding
from due.django.conformance._binding import entered_binding as _binding
from due.django.conformance.background import accounted_background_work, frozen_scheduler_clock
from due.django.conformance.bindings import (
    DurableWorkBindings,
    assert_registered_worker_options,
)
from due.django.conformance.composition import (
    CancellationExample,
    CompensationExample,
    ProjectionExample,
    assert_cancellation_after_entry_is_honest,
    assert_exhausted_producer_still_compensates,
    assert_late_projection_write_is_repaired,
    compensations_for,
)
from due.django.conformance.composition import (
    SupersessionExample as SupersessionExample,
)
from due.django.conformance.delivery import WorkTableDelivery
from due.django.conformance.discovery_admissibility import (
    assert_database_legal_candidate_is_admissible,
    assert_inadmissible_candidate_does_not_block_the_next,
    candidate_edges,
)
from due.django.conformance.indexes import assert_work_indexes
from due.django.conformance.observations import (
    OBSERVATION_HISTORIES,
    ObservationExample,
    assert_observation_recovers_without_resend,
)
from due.django.conformance.recovery_isolation import (
    assert_one_poisoned_identity_does_not_stall_recovery,
    recovery_isolation_histories,
)
from due.django.conformance.registrations import durable_work_installed, registered_capability
from due.django.conformance.scenarios import (
    EVIDENCE_RECOVERY_HISTORIES,
    SYNCHRONOUS_CALLER_HISTORIES,
    admission_atomicity,
    assert_producer_transaction,
    assert_receipt_repairs_product,
    assert_synchronous_caller,
    assert_uncertainty_blocks_resend,
    run_evidence_history,
)
from due.django.conformance.scenarios import (
    AdmissionFailureExample as AdmissionFailureExample,
)
from due.django.conformance.scenarios import (
    ProducerExample as ProducerExample,
)
from due.django.conformance.scenarios import (
    SynchronousCallerExample as SynchronousCallerExample,
)
from due.django.evidence.attempts import (
    attempts_for,
    observe_effect_outcome,
    record_effect_attempt,
)
from due.django.models import EffectAttempt, EffectAttemptOutcome
from due.django.public import (
    DurableWork,
    DurableWorkCapability,
    DurableWorkState,
    OwnedDurableWorkCapability,
    UniverseDurableWork,
    database_now,
)
from due.django.runtime.coordination import claim_work, outstanding_work
from due.django.runtime.registry import DurableWorkRegistration
from due.django.runtime.tasks import (
    recover_durable_work_task,
    run_durable_work_task,
)
from due.django.testing import expire_lease, hold_nudges, make_due
from due.errors import WorkFailureDisposition


@dataclass(frozen=True)
class DeliveryExample[W: UniverseDurableWork, ObservationT]:
    """A real producer's obligation and the visible outcome its worker owes."""

    work: W
    observe: Callable[[], ObservationT]
    expected: ObservationT
    effect_calls: Callable[[], int] | None = None


@dataclass(frozen=True)
class RejectionExample[W: UniverseDurableWork, ObservationT]:
    """A real provider rejection and its product outcome, composed with delivery setup."""

    delivery: DeliveryExample[W, ObservationT]
    disposition: WorkFailureDisposition
    expected_attempts: int = 1


@dataclass(frozen=True)
class EligibilityExample[W: UniverseDurableWork, ObservationT]:
    """A blocked admission, its real readiness transition and the owed outcome."""

    delivery: DeliveryExample[W, ObservationT]
    make_eligible: Callable[[], None]
    recovery_timeout: timedelta = timedelta(minutes=5)


@dataclass(frozen=True, kw_only=True)
class FactExamples[W: UniverseDurableWork]:
    """Canonical product fixtures/actions; no derivation or settlement plumbing."""

    make_implied: Callable[[], Any]
    move: Callable[[Any], Any]
    make_stopped: Callable[[], Any]
    desired: Callable[[W], Any]
    identity_after_move: Callable[[Any], Any] = lambda identity: identity


@dataclass(frozen=True)
class FailureExample[W: UniverseDurableWork]:
    attempts: Callable[[W], int]
    prepare: Callable[[W], object] = lambda work: None


@dataclass(frozen=True, kw_only=True)
class TerminalExample[W: UniverseDurableWork]:
    """
    What the product shows once the runtime gives up, and how it gets another chance.

    ``observe`` reads product state (not the work row) and must change when the
    work exhausts: an operator or user has to be able to see the failure.
    ``rearm`` is the authorized product command that re-opens the stopped work,
    normally ``with capability.atomic(owner_id) as pending: pending.retry_failed()``;
    the harness checks it starts a fresh revision and budget. A capability that
    must never re-arm (an immutable provider occurrence) states
    ``no_rearm_because`` instead.
    """

    observe: Callable[[W], Any]
    rearm: Callable[[W], object] | None = None
    no_rearm_because: str | None = None

    def __post_init__(self) -> None:
        if (self.rearm is None) == (self.no_rearm_because is None):
            raise ValueError("terminal example must declare exactly one of rearm or no_rearm_because")
        if self.no_rearm_because is not None and not is_real_reason(self.no_rearm_because):
            raise ValueError("no_rearm_because must name the product rule that forbids re-arming")


@dataclass(frozen=True, kw_only=True)
class EffectExamples[W: UniverseDurableWork]:
    in_flight: Mapping[str, Scenario] = field(default_factory=dict)
    evidence_confluence: Mapping[str, Scenario] = field(default_factory=dict)
    make_work: Callable[[], W]
    delivery: Binding[DeliveryExample[W, Any]]
    retry: Binding[FailureExample[W]]
    terminal: Binding[TerminalExample[W]] | None = None
    replay: Binding[ReplaySafeEffect] | None = None
    eligibility: Binding[EligibilityExample[W, Any]] | Mapping[str, Binding[EligibilityExample[W, Any]]] | None = None
    immutable_inputs: str | None = None
    snapshot: Binding[SupersessionExample[W, Any]] | None = None
    derivation: Binding[FactExamples[W]] | None = None
    applied_receipt: Callable[[W], Any] | None = None
    receipt_repair: Binding[DeliveryExample[W, Any]] | None = None
    producers: dict[str, Binding[ProducerExample[W, Any]]] = field(default_factory=dict)
    admissions: dict[str, Binding[ProducerExample[W, Any]]] = field(default_factory=dict)
    admission_failures: dict[str, Binding[AdmissionFailureExample[W, Any]]] = field(default_factory=dict)
    # Profile I is NotApplicable only when admission has no partial state to roll
    # back: one work-row write, committed atomically with no product row. A
    # command that also writes product rows binds admission_failures.
    admission_not_applicable: str | None = None
    # Profile E, MONOTONIC_RESULTS: the production result-application transition.
    convergence: Binding[ConvergentWrite] | None = None
    # Profile E families that genuinely do not apply; an unbound, undeclared family stays NotAssessed.
    convergence_families: Mapping[ConvergenceFamily, NotApplicable] = field(default_factory=dict)
    # Request-path entry points that admit and then attempt inline; see SynchronousCallerExample.
    synchronous_callers: dict[str, Binding[SynchronousCallerExample[Any]]] = field(default_factory=dict)
    rejections: Mapping[str, Binding[RejectionExample[W, Any]]] | None = None
    uncertainties: Mapping[str, Binding[DeliveryExample[W, Any]]] | None = None
    observation: Binding[ObservationExample[W, Any, Any]] | None = None
    identity_of: Callable[[W], Any] | None = None
    environment: Callable[[], AbstractContextManager] = nullcontext
    fixtures: tuple[str, ...] = ()
    compensation: Binding[CompensationExample[W, Any]] | None = None
    # One shared fault runner for native and work-table contracts: this adds
    # product bindings, not a second recovery loop. The registered sweep runs
    # the real workers even when the transition hands off to another capability.
    handoffs: tuple[HandoffHistory[Any, Any], ...] = ()
    process_handoffs: tuple[ProcessHistory[Any, Any], ...] = ()


def assert_registered_schedule(entry: DurableWorkRegistration) -> None:
    """Inspect installed configuration, never the schedule generator's return value."""
    task_path = "due.django.runtime.tasks.recover_durable_work_task"
    from celery import current_app as celery_app

    assert "due.django.runtime.tasks" in celery_app.conf.imports
    installed = [
        item
        for item in settings.CELERY_BEAT_SCHEDULE.values()
        if item["task"] == task_path and tuple(item.get("args", ())) == (entry.name,)
    ]
    assert len(installed) == 1, f"{entry.name}: require exactly one capability-specific Beat entry"
    actual = installed[0]
    assert actual["schedule"] == entry.cadence_seconds, f"{entry.name}: recovery cadence drifted"
    assert actual["options"]["queue"] == entry.queue, f"{entry.name}: recovery queue drifted"
    assert actual["options"]["expire_seconds"] == entry.cadence_seconds, f"{entry.name}: recovery expiry drifted"
    # The settings dict is what ready() populated; Beat reads the Celery app's
    # view of it. Checking only the dict would certify the generator with itself.
    from celery import current_app as celery_app

    live = [
        item
        for item in celery_app.conf.beat_schedule.values()
        if item["task"] == task_path and tuple(item.get("args", ())) == (entry.name,)
    ]
    assert live == installed, f"{entry.name}: the Celery app does not see the generated Beat entry"
    capability = registered_capability(entry.name)
    assert capability.model is entry.model


def _in_environment[T](examples: EffectExamples, factory: Binding[T]) -> Binding[T]:
    @contextmanager
    @wraps(factory)
    def bound():
        with examples.environment(), _binding(factory) as value:
            yield value

    return bound


@contextmanager
def fact_bindings[OwnerT: models.Model, W: UniverseDurableWork, ResultT](
    capability: OwnedDurableWorkCapability[OwnerT, W, ResultT], factory: Binding[FactExamples[W]]
) -> Iterator[StateDerived]:
    with _binding(factory) as facts:
        model, owner_field = capability.model, capability.owner_attname

        def work_for(identity):
            return model.objects.get(**{owner_field: identity})

        def settle(identity):
            work = work_for(identity)
            capability.attempt_execution(work.pk, work.fencing_token if work.state == DurableWorkState.QUEUED else None)

        def age(identity):
            model.objects.filter(**{owner_field: identity}).update(available_at=database_now() - timedelta(seconds=1))

        yield StateDerived(
            name=capability.model._meta.label_lower,
            make_implied_obligation=facts.make_implied,
            derive=capability.derive,
            settle=settle,
            move_desired_state=facts.move,
            make_stopped=facts.make_stopped,
            identity_after_move=facts.identity_after_move,
            outstanding=lambda: list(outstanding_work(model).values_list(owner_field, flat=True)),
            record_of=lambda identity: model.objects.filter(**{owner_field: identity}).values().first(),
            desired_of=lambda identity: facts.desired(work_for(identity)),
            make_recovery_eligible=age,
        )


@contextmanager
def failing_external_call(path: str) -> Iterator[FailureExample]:
    counts = Counter()

    def fail(work):
        counts[work.pk] += 1
        raise OSError("injected provider outage")

    with patch(path, fail):
        yield FailureExample(attempts=lambda work: counts[work.pk])


@contextmanager
def retry_bindings[W: UniverseDurableWork, ResultT](
    bindings: DurableWorkBindings[W, ResultT], factory: Binding[FailureExample[W]]
) -> Iterator[BoundedRetry]:
    with _binding(factory) as failure:

        def make():
            work = bindings.make()
            failure.prepare(work)
            return work

        def execute(work):
            with suppress(Exception):
                bindings.capability.attempt_execution(work.pk)

        assert bindings.capability.policy.max_rearms is not None
        yield BoundedRetry(
            name=bindings.registration.name,
            max_executions=bindings.capability.policy.max_rearms + 1,
            make_failing=make,
            due_work=lambda: bindings.capability.due_work(),
            run_once=execute,
            advance_to_due=bindings.age,
            is_terminal=lambda work: bindings.observe(work)[0] == DurableWorkState.EXHAUSTED,
            failure_attempt_count=failure.attempts,
            observe=bindings.observe,
            identity_of=lambda work: work.pk,
        )


@contextmanager
def snapshot_bindings[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT], factory: Binding[SupersessionExample[W, Any]]
) -> Iterator[SupersededSnapshot]:
    sent = []
    with (
        hold_nudges(),
        patch(
            "due.django.runtime.tasks.run_durable_work_task.apply_async",
            side_effect=lambda **kwargs: sent.append(kwargs["args"]),
        ),
        _binding(factory) as example,
    ):

        def capture():
            capability.enqueue_execution(example.work.pk)
            return sent.pop()

        yield SupersededSnapshot(
            name=capability.model._meta.label_lower,
            take_snapshot=capture,
            supersede=example.change,
            run_with_snapshot=lambda args: run_durable_work_task(*args),
            observe=example.observe,
        )


def projection_retention[W: UniverseDurableWork, ResultT](bindings: DurableWorkBindings[W, ResultT]) -> Retention:
    capability = bindings.capability
    assert isinstance(capability, DurableProjection)

    def attempt(resolved=False):
        work = bindings.make()
        claim = claim_work(type(work), work.pk, lease_duration=capability.policy.lease_duration)
        assert claim is not None
        value = record_effect_attempt(claim)
        if resolved:
            observe_effect_outcome(value, EffectAttemptOutcome.NOT_APPLIED)
            EffectAttempt.objects.filter(pk=value.pk).update(
                observed_at=database_now() - capability.attempt_retention - timedelta(days=1)
            )
        return value

    return Retention(
        name=capability.work_name,
        make_non_terminal=attempt,
        make_prunable=lambda: attempt(True),
        run_retention=capability.derive,
        still_exists=lambda value: EffectAttempt.objects.filter(pk=value.pk).exists(),
    )


@contextmanager
def operation_ambiguity[W: UniverseDurableWork, ResultT](
    bindings: DurableWorkBindings[W, ResultT], receipt: Callable[[W], Any]
) -> Iterator[AmbiguityAware]:
    capability = bindings.capability
    assert isinstance(capability, DurableExternalOperation)
    claims, attempts = {}, {}
    model = capability.model

    def claim():
        work = bindings.make()
        claimed = claim_work(model, work.pk, lease_duration=capability.policy.lease_duration)
        assert claimed is not None
        claims[claimed.token] = claimed
        return work.pk, claimed.token

    def start(pk, token):
        value = record_effect_attempt(claims[token], before_commit=capability._before_attempt_commits)
        attempts[pk] = value
        return value

    def state(pk):
        return model.objects.values_list("state", flat=True).get(pk=pk)

    def resolve(pk):
        capability.recover()
        return state(pk)

    def apply_late(pk):
        capability.record_applied_effect_evidence(attempts[pk], receipt(model.objects.get(pk=pk)))
        capability.settle_from_applied_effect_evidence(pk)
        return state(pk)

    def expire(pk) -> None:
        model.objects.filter(pk=pk).update(lease_expires_at=database_now() - timedelta(seconds=1))

    with patch("due.django.runtime.tasks.run_durable_work_task.apply_async"):
        yield AmbiguityAware(
            name=capability.work_name,
            claim=claim,
            start_attempt=start,
            open_attempt=lambda pk: (
                attempts_for(model.objects.get(pk=pk)).filter(outcome=EffectAttemptOutcome.UNKNOWN).first()
            ),
            expire_lease=expire,
            resolve_stalled=resolve,
            ambiguous_disposition=DurableWorkState.UNKNOWN,
            retryable_disposition=DurableWorkState.RETRYABLE,
            state_of=state,
            ambiguous_state=DurableWorkState.UNKNOWN,
            terminal_states=(
                DurableWorkState.SUCCEEDED,
                DurableWorkState.TERMINAL_FAILED,
                DurableWorkState.EXHAUSTED,
            ),
            apply_late_evidence=apply_late,
            due_work_ids=lambda: capability.due_work().values_list("pk", flat=True),
        )


MODES = (
    DurableExternalOperation,
    DurableProjection,
    DurableScheduledAction,
    IndependentCompensation,
    ReplaySafeCapability,
    OwnedDurableWorkCapability,
)


def required_examples_for(mode: type[DurableWorkCapability[Any, Any]]) -> tuple[str, ...]:
    """Unconditional proof requirements of a mode; application features add obligations below."""
    common = ("delivery", "retry")
    if issubclass(mode, DurableExternalOperation):
        required = (*common, "applied_receipt", "receipt_repair", "rejections", "uncertainties")
        return (*required, "observation") if issubclass(mode, ObservedExternalOperation) else required
    if issubclass(mode, (DurableProjection, DurableScheduledAction)):
        return (*common, "replay", "snapshot")
    if issubclass(mode, (IndependentCompensation, ReplaySafeCapability, OwnedDurableWorkCapability)):
        return (*common, "replay")
    raise ObligationContractDesignError(f"unrecognized capability mode: {mode.__name__}")


def effect_contract[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT], *, examples: EffectExamples[W]
) -> ObligationContract:
    """Features create proof obligations. No profile list is supplied by the domain."""
    discovery = isinstance(capability, OwnedDurableWorkCapability) and capability.discovery is not None
    identity = examples.identity_of
    if identity is None:
        identity = (
            (lambda work: getattr(work, capability.owner_attname))
            if isinstance(capability, OwnedDurableWorkCapability) and discovery
            else (lambda work: work.pk)
        )
    bindings = DurableWorkBindings(capability, examples.make_work, identity_of=identity)
    name = bindings.registration.name
    operation = isinstance(capability, DurableExternalOperation)
    moving = isinstance(capability, (DurableProjection, DurableScheduledAction)) or (discovery and not operation)
    moving = moving or examples.snapshot is not None
    pruning = capability.policy.retention is not None or isinstance(capability, DurableProjection)
    required = {
        **dict.fromkeys(required_examples_for(type(capability)), True),
        "terminal": capability.policy.max_rearms is not None,
        "snapshot": moving,
        "derivation": discovery,
        "compensation": bool(compensations_for(capability)),
        "eligibility": capability.eligibility is not None,
    }
    missing = [field for field, needed in required.items() if needed and getattr(examples, field) is None]
    if not moving and not examples.immutable_inputs:
        missing.append("immutable_inputs (domain reason why input cannot be superseded)")
    if missing:
        raise ObligationContractDesignError(f"{name}: production semantics require examples: {', '.join(missing)}")
    for scenario_name, scenario_bindings in (
        ("rejections", examples.rejections),
        ("uncertainties", examples.uncertainties),
    ):
        if operation and scenario_bindings is not None and not scenario_bindings:
            raise ObligationContractDesignError(f"{name}: {scenario_name} needs at least one real provider scenario")
        if not operation and scenario_bindings is not None:
            raise ObligationContractDesignError(
                f"{name}: {scenario_name} examples require the operation execution mode"
            )
    if examples.observation is not None and not isinstance(capability, ObservedExternalOperation):
        raise ObligationContractDesignError(f"{name}: observation examples require the observed operation mode")
    if operation and examples.replay is not None:
        raise ObligationContractDesignError(f"{name}: evidence-required operations cannot claim blind replay safety")
    if capability.eligibility is None and examples.eligibility is not None:
        raise ObligationContractDesignError(f"{name}: eligibility examples require a production execution gate")
    if not discovery and examples.derivation is not None:
        raise ObligationContractDesignError(f"{name}: derivation examples require registered production discovery")
    if not operation and examples.applied_receipt is not None:
        raise ObligationContractDesignError(f"{name}: ambiguity examples require the operation execution mode")
    if examples.admission_not_applicable is not None:
        if examples.admission_failures:
            raise ObligationContractDesignError(
                f"{name}: admission_not_applicable contradicts the admission_failures it declares"
            )
        if not is_real_reason(examples.admission_not_applicable):
            raise ObligationContractDesignError(
                f"{name}: admission_not_applicable must name the single atomic work-row write"
            )
    convergent = moving or bool(examples.in_flight or examples.evidence_confluence) or examples.convergence is not None
    if examples.convergence_families and not convergent:
        raise ObligationContractDesignError(
            f"{name}: convergence_families decide families of a profile E it never claims"
        )
    for family, decision in examples.convergence_families.items():
        if not isinstance(decision, NotApplicable) or not is_real_reason(decision.because):
            raise ObligationContractDesignError(
                f"{name}: convergence family {family.name} needs NotApplicable with its domain reason"
            )
    retention = None
    if capability.policy.retention is not None:
        retention = bindings.retention
    elif isinstance(capability, DurableProjection):

        def retention():
            # ARRANGE: domain examples provide genuine work for projection retention.
            # REAL PRODUCTION: the projection retention binding calls this registered capability.
            # EXTERNAL SEAM: only the example's provider/fault seam is substituted.
            # OBSERVE: independent product observations and persisted work/evidence.
            return projection_retention(bindings)

    derived = None
    if examples.derivation is not None:
        assert isinstance(capability, OwnedDurableWorkCapability)

        def derived():
            # ARRANGE: domain examples provide genuine work for canonical discovery.
            # REAL PRODUCTION: the canonical discovery binding calls this registered capability.
            # EXTERNAL SEAM: only the example's provider/fault seam is substituted.
            # OBSERVE: independent product observations and persisted work/evidence.
            assert isinstance(capability, OwnedDurableWorkCapability)
            assert examples.derivation is not None
            return fact_bindings(capability, examples.derivation)

    def retry():
        # ARRANGE: domain examples provide genuine work for retry executor.
        # REAL PRODUCTION: the retry executor binding calls this registered capability.
        # EXTERNAL SEAM: only the example's provider/fault seam is substituted.
        # OBSERVE: independent product observations and persisted work/evidence.
        return retry_bindings(bindings, examples.retry)

    snapshot = None
    if examples.snapshot is not None:

        def snapshot():
            # ARRANGE: domain examples provide genuine work for revision fencing.
            # REAL PRODUCTION: the revision fencing binding calls this registered capability.
            # EXTERNAL SEAM: only the example's provider/fault seam is substituted.
            # OBSERVE: independent product observations and persisted work/evidence.
            assert examples.snapshot is not None
            return snapshot_bindings(capability, examples.snapshot)

    ambiguity = None
    if examples.applied_receipt is not None:

        def ambiguity():
            # ARRANGE: domain examples provide genuine work for provider evidence.
            # REAL PRODUCTION: the provider evidence binding calls this registered capability.
            # EXTERNAL SEAM: only the example's provider/fault seam is substituted.
            # OBSERVE: independent product observations and persisted work/evidence.
            assert examples.applied_receipt is not None
            return operation_ambiguity(bindings, examples.applied_receipt)

    def bound[T](factory: Binding[T] | None) -> Binding[T] | None:
        return _in_environment(examples, factory) if factory else None

    def gate_binding(factory: Binding[EligibilityExample[W, Any]]) -> ExecutionGateBinding:
        def gate():
            # ARRANGE: domain-provided prerequisites and work.
            # REAL PRODUCTION: eligibility_binding calls the registered recovery and executor.
            # EXTERNAL SEAM: the domain example controls the external dependency.
            # OBSERVE: product outcome, provider calls and outstanding work.
            return eligibility_binding(capability, examples, factory)

        return gate

    eligibility = examples.eligibility
    gates = None
    if isinstance(eligibility, Mapping):
        gates = {key: gate_binding(factory) for key, factory in eligibility.items()}
    elif eligibility is not None:
        gates = gate_binding(eligibility)

    def admission_binding(
        factory: Binding[AdmissionFailureExample[W, Any]],
    ) -> Callable[[], AbstractContextManager[AdmissionAtomicity]]:
        @contextmanager
        def entered() -> Iterator[AdmissionAtomicity]:
            # ARRANGE: existing product state from the application example.
            # REAL PRODUCTION: standalone application admission command.
            # EXTERNAL SEAM: declared partial-write fault and held Celery publication.
            # OBSERVE: product state, complete work set, publications and provider calls.
            with _binding(factory) as example:
                yield admission_atomicity(capability, example)

        return entered

    contract = ObligationContract(
        name=name,
        transactional=True,
        fixtures=examples.fixtures,
        profiles={
            Profile.G: Claim() if gates else NotApplicable("The production capability has no execution gate."),
            Profile.I: Claim()
            if examples.admission_failures
            else NotApplicable(examples.admission_not_applicable)
            if examples.admission_not_applicable
            else NotAssessed(because="No standalone command failure example exercises partial admission rollback."),
            Profile.A: Claim(),
            Profile.B: Claim(),
            Profile.C: Claim()
            if operation
            else NotApplicable("Replay-safe mode requires the separately generated actual external replay proof."),
            Profile.D: Claim()
            if pruning
            else NotApplicable("Registered policy retains work and evidence indefinitely."),
            Profile.E: Claim() if convergent else NotApplicable(examples.immutable_inputs or ""),
            Profile.F: Claim() if discovery else NotApplicable("No canonical discovery is registered."),
            Profile.HARMLESS_REPLAY: Decline(
                "Production requires definite provider evidence; another call can duplicate an occurrence."
            )
            if operation
            else Claim(),
            Profile.JOB_RETRY_LIMITS: Claim()
            if capability.policy.max_rearms is not None
            else Decline("The application retains this obligation until success; execution retries are unbounded."),
        },
        sweep=bound(bindings.sweep),
        ownership=bound(bindings.ownership),
        retention=bound(retention),
        snapshot=bound(snapshot),
        derivation=bound(derived),
        ambiguity=bound(ambiguity),
        eligibility=gates,
        admission={name: admission_binding(factory) for name, factory in examples.admission_failures.items()},
        convergence=bound(examples.convergence),
        convergence_families=examples.convergence_families,
        in_flight=examples.in_flight,
        evidence_confluence=examples.evidence_confluence,
        handoffs=examples.handoffs,
        process_handoffs=examples.process_handoffs,
        handoff_delivery=WorkTableDelivery(capability) if examples.handoffs else None,
        replay=bound(examples.replay),
        retry=bound(retry) if capability.policy.max_rearms is not None else None,
    )
    return contract


@contextmanager
def eligibility_binding[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT],
    examples: EffectExamples[W],
    factory: Binding[EligibilityExample[W, Any]],
) -> Iterator[ExecutionGate]:

    bindings = DurableWorkBindings(capability, examples.make_work)
    entry = bindings.registration
    assert capability.eligibility is not None
    mutations: list[str] = []

    def record_mutation(execute: Callable[..., Any], sql: str, params: Any, many: bool, context: dict[str, Any]) -> Any:
        result, committed = execute_reporting_autocommit_write(execute, sql, params, many, context)
        if committed or row_write(sql, context["cursor"]) is not None:
            mutations.append(sql)
        return result

    with (
        pytest.MonkeyPatch.context() as patches,
        frozen_scheduler_clock() as clock,
        accounted_background_work(patches, clock) as driver,
        examples.environment(),
        hold_nudges(),
        _binding(factory) as scenario,
        connection.execute_wrapper(record_mutation),
    ):
        delivery = scenario.delivery
        work = delivery.work
        assert delivery.effect_calls is not None, "eligibility proof needs independent provider-call counts"

        def reserved_state() -> tuple[object, ...]:
            work.refresh_from_db()
            return (
                bindings.observe(work),
                work.available_at,
                work.desired_revision,
                work.attempt_count,
                work.rearm_count,
            )

        def advance(delta: timedelta) -> None:
            driver.advance(delta.total_seconds())

        yield ExecutionGate(
            name=entry.name,
            identity=work.pk,
            due_work=lambda: capability.due_work().values_list("pk", flat=True),
            owed_work=lambda: outstanding_work(capability.model).values_list("pk", flat=True),
            reserved_state=reserved_state,
            routes={
                "direct": lambda: capability.attempt_execution(work.pk),
                "immediate": lambda: capability.execute_now(work),
                "enqueue": lambda: capability.enqueue_execution(work.pk),
            },
            make_eligible=scenario.make_eligible,
            recover=lambda: driver.run_recovery(entry.name),
            advance=advance,
            executions=lambda: sum(item.delivered for item in driver.work_deliveries(entry.name)),
            mutation_count=lambda: len(mutations),
            observe=delivery.observe,
            expected=delivery.expected,
            effect_calls=delivery.effect_calls,
            recovery_interval=timedelta(seconds=entry.cadence_seconds),
            recovery_timeout=scenario.recovery_timeout,
            recheck_after=capability.eligibility.recheck_after,
        )


def assert_lost_notification_reaches_domain_outcome[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT], examples: EffectExamples[W]
) -> None:
    bindings = DurableWorkBindings(capability, examples.make_work)
    entry = bindings.registration
    assert_registered_schedule(entry)
    sent = []

    def capture(*, args, **options):
        if args[0] == entry.name:
            sent.append((args, options))

    with (
        examples.environment(),
        hold_nudges(),
        patch("due.django.runtime.tasks.run_durable_work_task.apply_async", capture),
        _binding(examples.delivery) as example,
    ):
        assert type(example.work) is capability.model, "producer returned another capability's work"
        assert example.observe() != example.expected, (
            "positive control: the example must initially owe a visible effect"
        )
        assert not sent, "producer must record intent without inline worker execution"
        example.work.refresh_from_db()
        never_executed = example.work.state in (DurableWorkState.READY, DurableWorkState.QUEUED)
        bindings.age(example.work)
        # Compensation producers reserve cleanup before I/O. Losing that worker
        # includes lease expiry; this is clock fault injection, not settlement.
        capability.model.objects.filter(pk=example.work.pk, lease_expires_at__isnull=False).update(
            lease_expires_at=database_now() - timedelta(seconds=1)
        )
        recover_durable_work_task(entry.name)
        # Reaping preserves the configured retry delay. Advance only its clock.
        example.work.refresh_from_db()
        if example.work.state == DurableWorkState.RETRYABLE:
            bindings.age(example.work)
            recover_durable_work_task(entry.name)
        deliveries = [(args, options) for args, options in sent if args[1] == example.work.pk]
        assert len(deliveries) == 1, f"{entry.name}: configured recovery did not publish the producer's exact work"
        args, options = deliveries[0]
        assert_registered_worker_options(entry, options)
        assert run_durable_work_task(*args), "captured production delivery was not executable"
        assert_observation(example.observe(), example.expected, because="worker reaches the required domain outcome")
        example.work.refresh_from_db()
        if never_executed:
            assert example.work.rearm_count == 0, (
                f"{entry.name}: a lost notification spent the retry budget although no worker ever ran"
            )


def assert_two_workers_one_settlement[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT], examples: EffectExamples[W]
) -> None:
    """Use the existing real delivery example for competing worker connections."""
    from due.django.conformance.concurrency import assert_competing_deliveries

    bindings = DurableWorkBindings(capability, examples.make_work)
    with (
        examples.environment(),
        hold_nudges(),
        patch("due.django.runtime.tasks.run_durable_work_task.apply_async"),
        _binding(examples.delivery) as example,
    ):
        work = example.work
        work.refresh_from_db()
        if work.state == DurableWorkState.CLAIMED:
            # Independent compensation is created claimed. Recover that lost
            # producer before racing two copies of its cleanup delivery.
            expire_lease(work)
            capability.recover()
        bindings.age(work)
        capability.enqueue_execution(work.pk)
        work.refresh_from_db()
        assert work.state == DurableWorkState.QUEUED, "positive control: reserve a real delivery before racing it"
        assert_competing_deliveries(capability, work, observe=example.observe, expected=example.expected)


def assert_lost_delivery_spends_no_retry_budget[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT], examples: EffectExamples[W]
) -> None:
    """A reservation the broker lost is redelivered by recovery with the budget untouched."""
    bindings = DurableWorkBindings(capability, examples.make_work)
    entry = bindings.registration
    sent = []

    def capture(*, args, **options):
        if args[0] == entry.name:
            sent.append((args, options))

    with (
        examples.environment(),
        hold_nudges(),
        patch("due.django.runtime.tasks.run_durable_work_task.apply_async", capture),
        _binding(examples.delivery) as example,
    ):
        work = example.work
        bindings.age(work)
        capability.enqueue_execution(work.pk)  # The broker accepts and then loses this message.
        work.refresh_from_db()
        assert work.state == DurableWorkState.QUEUED, "positive control: the reservation must be recorded"
        lost = [args for args, _ in sent if args[1] == work.pk]
        assert len(lost) == 1
        capability.model.objects.filter(pk=work.pk).update(lease_expires_at=database_now() - timedelta(seconds=1))
        recover_durable_work_task(entry.name)
        work.refresh_from_db()
        assert work.rearm_count == 0, f"{entry.name}: an undelivered reservation spent the retry budget"
        assert work.state == DurableWorkState.QUEUED, f"{entry.name}: recovery did not redeliver lost work"
        redelivered = [args for args, _ in sent if args[1] == work.pk]
        assert len(redelivered) == 2, f"{entry.name}: recovery must publish exactly one replacement delivery"
        assert not run_durable_work_task(*lost[0]), "the lost token must not start execution later"
        assert run_durable_work_task(*redelivered[-1]), "the redelivery was not executable"
        assert_observation(
            example.observe(), example.expected, because="redelivered worker reaches the required domain outcome"
        )


def assert_terminal_failure_is_visible_and_rearmable[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT], examples: EffectExamples[W]
) -> None:
    """Exhaustion must change product-visible state, and a same-input re-request must re-arm (or say why not)."""
    bindings = DurableWorkBindings(capability, examples.make_work)
    entry = bindings.registration
    assert examples.terminal is not None
    assert capability.policy.max_rearms is not None, "unbounded policies never exhaust; no terminal proof applies"
    with (
        examples.environment(),
        hold_nudges(),
        patch("due.django.runtime.tasks.run_durable_work_task.apply_async"),
        _binding(examples.retry) as failure,
        _binding(examples.terminal) as terminal,
    ):
        work = bindings.make()
        failure.prepare(work)
        before = terminal.observe(work)
        for _ in range(capability.policy.max_rearms + 1):
            with suppress(Exception):
                capability.attempt_execution(work.pk)
            work.refresh_from_db()
            if work.state in FAILED_TERMINAL_STATES:
                break
            assert work.state == DurableWorkState.RETRYABLE, (
                f"{entry.name}: the retry example must fail with a classification the runtime retries, got {work.state}"
            )
            bindings.age(work)
        assert work.state in FAILED_TERMINAL_STATES, (
            f"{entry.name}: positive control: the failing provider must exhaust"
        )
        assert failure.attempts(work) >= 1, "positive control: the injected failure was never reached"
        after = terminal.observe(work)
        assert after != before, (
            f"{entry.name}: exhaustion left no product-visible consequence; publish it from finalize_retry or "
            "finalize_terminal so users and operators can see the failure"
        )
        if terminal.rearm is None:
            revision = work.desired_revision
            # The declared rule: this capability never re-arms; a re-request would be a new occurrence.
            work.refresh_from_db()
            assert work.desired_revision == revision and work.state in FAILED_TERMINAL_STATES
            return
        revision = work.desired_revision
        terminal.rearm(work)
        work.refresh_from_db()
        assert work.desired_revision > revision, (
            f"{entry.name}: re-request after exhaustion did not start a new revision"
        )
        assert work.rearm_count == 0, f"{entry.name}: re-armed work kept the exhausted budget"
        assert work.state in (DurableWorkState.READY, DurableWorkState.QUEUED), (
            f"{entry.name}: re-armed work is not due again ({work.state})"
        )
        assert work.pk in {pk for pk in capability.due_work().values_list("pk", flat=True)} or (
            work.state == DurableWorkState.QUEUED
        ), f"{entry.name}: re-armed work is not selected by production recovery"


def assert_settlement_wakes_declared_dependents[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT],
    delivery: Binding[DeliveryExample[W, Any]],
    environment: Callable[[], AbstractContextManager] = nullcontext,
) -> None:
    """
    The real live settlement wakes each declared dependent once, after its own locks.

    A dependent that was QUEUED is left to its delivery and publishes nothing;
    any other state publishes exactly one notification for that row. The
    recorded transactions must lock the upstream work row before the
    dependent's, and none may lock them in the other order.
    """
    name = capability.model._meta.label_lower
    upstream_table = capability.model._meta.db_table
    with environment(), hold_nudges() as held, _binding(delivery) as example:
        work = example.work
        dependents: dict[type[UniverseDurableWork], DurableWork] = {}
        for dependent in capability.wakes:
            dependent_id = dependent.via(work)
            assert dependent_id is not None, (
                f"{name}: the delivery example must admit the {dependent.work._meta.label_lower} it wakes, "
                "or the proof observes nothing"
            )
            dependents[dependent.work] = dependent.work.objects.get(pk=dependent_id)
        make_due(work)
        held.clear()
        with recording_against_the_ledger(f"{name}: settlement wakes declared dependents") as recorder:
            capability.execute_now(work)
        assert_observation(example.observe(), example.expected, because="the wake rides a successful settlement")
        for model, before in dependents.items():
            label, table = model._meta.label_lower, model._meta.db_table
            published = sum(1 for nudge in held if (nudge.work_name, nudge.work_id) == (label, before.pk))
            expected = 0 if before.state == DurableWorkState.QUEUED else 1
            assert published == expected, (
                f"{name}: settlement published {published} wake(s) for {label}:{before.pk} "
                f"in state {before.state}; expected {expected}"
            )
            ordered = [item.tables for item in recorder.sequences if {upstream_table, table} <= set(item.tables)]
            assert ordered, f"{name}: positive control: no settlement transaction locked {table}"
            reversed_order = [tables for tables in ordered if tables.index(table) < tables.index(upstream_table)]
            assert not reversed_order, f"{name}: {table} was locked before {upstream_table}: {reversed_order}"


def assert_lock_order_is_consistent[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT], examples: EffectExamples[W]
) -> None:
    """Record row-lock order across the generated scenarios and check it against the process ledger."""
    entry = DurableWorkBindings(capability, examples.make_work).registration
    # Each scenario is checked before it joins the ledger, so a rejected cycle fails this case only.
    with recording_against_the_ledger(f"{entry.name}: lost notification"):
        assert_lost_notification_reaches_domain_outcome(capability, examples)
    if capability.policy.max_rearms is not None:
        with recording_against_the_ledger(f"{entry.name}: exhaustion and re-arm"):
            assert_terminal_failure_is_visible_and_rearmable(capability, examples)


def assert_rejection_survives_recovery[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT],
    factory: Binding[RejectionExample[W, Any]],
    *,
    interruption: str,
) -> None:
    """Recover the same decision across evidence loss and settlement rollback."""
    assert isinstance(capability, DurableExternalOperation)
    with hold_nudges(), _binding(factory) as scenario:
        work = scenario.delivery.work
        work.refresh_from_db()
        initial_rearms = work.rearm_count
        effect_calls = scenario.delivery.effect_calls
        assert effect_calls is not None, "rejection recovery requires an independent provider-call observation"
        assert effect_calls() == 0
        run_evidence_history(
            capability,
            scenario.delivery,
            record_evidence=capability.record_rejection,
            record_method="record_rejection",
            finalizer_method="_finalize_terminal"
            if scenario.disposition == WorkFailureDisposition.TERMINAL
            else "_finalize_retry",
            finalize=capability._finalize_terminal
            if scenario.disposition == WorkFailureDisposition.TERMINAL
            else capability._finalize_retry,
            interruption=interruption,
        )
        capability.recover()
        work.refresh_from_db()
        expected_state = (
            DurableWorkState.TERMINAL_FAILED
            if scenario.disposition == WorkFailureDisposition.TERMINAL
            else DurableWorkState.EXHAUSTED
            if capability.policy.max_rearms is not None and initial_rearms >= capability.policy.max_rearms
            else DurableWorkState.RETRYABLE
        )
        assert work.state == expected_state, "recovery must preserve the rejection decision"
        assert_observation(
            scenario.delivery.observe(),
            scenario.delivery.expected,
            because="rejection recovery preserves the required product outcome",
        )
        assert effect_calls() == scenario.expected_attempts, (
            "settling rejection evidence must not call the provider again"
        )
        evidence = attempts_for(work).get(desired_revision=work.desired_revision, claim_token=work.fencing_token)
        assert evidence.outcome == EffectAttemptOutcome.NOT_APPLIED
        decision = capability.rejection_type.model_validate(evidence.rejection)
        assert decision.disposition == scenario.disposition
        rearms = initial_rearms + (expected_state == DurableWorkState.RETRYABLE)
        assert work.rearm_count == rearms, "one rejection must consume exactly one available retry"
        capability.recover()
        work.refresh_from_db()
        assert work.rearm_count == rearms, "recovery must not spend the same rejection's budget twice"
        assert_observation(
            scenario.delivery.observe(),
            scenario.delivery.expected,
            because="rejection recovery preserves the required product outcome",
        )
        assert effect_calls() == scenario.expected_attempts


def effect_contract_suite[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT],
    *,
    examples: EffectExamples[W],
    covers: tuple[DueWorkSource, ...] = (),
) -> Callable[[type], type]:
    from due.django.conformance.collected_effects import enforce_proof_outcome
    from due.django.conformance.suite_collection import collect_suite

    contract = effect_contract(capability, examples=examples)
    entry = DurableWorkBindings(capability, examples.make_work).registration
    compensations = compensations_for(capability)
    if len(compensations) > 1:
        raise ObligationContractDesignError(
            "multiple compensation relationships require one scenario for each relationship"
        )

    def decorate(cls: type) -> type:
        cls = due_work_contract_suite(contract, covers=covers)(cls)
        cls.__durable_work_capability__ = capability
        cls.__durable_work_examples__ = examples
        cls.__durable_work_collect__ = staticmethod(collect_suite)
        cls.__durable_work_outcome__ = staticmethod(enforce_proof_outcome)

        # The proofs drive production entry points, and every production process installs the
        # registrations during application startup.
        @pytest.fixture(autouse=True)
        def wired_durable_work(self) -> Iterator[None]:
            with durable_work_installed():
                yield

        cls.wired_durable_work = wired_durable_work

        @pytest.mark.django_db(transaction=True)
        def test_registered_producer_lost_notification(self):
            assert_lost_notification_reaches_domain_outcome(capability, examples)

        if examples.fixtures:
            test_registered_producer_lost_notification = pytest.mark.usefixtures(*examples.fixtures)(
                test_registered_producer_lost_notification
            )

        def test_registered_effect_enrollment(self):
            assert registered_capability(entry.name) is capability
            assert_registered_schedule(entry)

        cls.test_registered_producer_lost_notification = test_registered_producer_lost_notification
        cls.test_registered_effect_enrollment = test_registered_effect_enrollment

        def with_fixtures(test):
            return pytest.mark.usefixtures(*examples.fixtures)(test) if examples.fixtures else test

        @pytest.mark.django_db(transaction=True)
        def test_migrated_work_indexes(self):
            assert_work_indexes(capability.model)

        cls.test_migrated_work_indexes = with_fixtures(test_migrated_work_indexes)

        if isinstance(capability, DurableExternalOperation):

            @pytest.mark.django_db(transaction=True)
            @pytest.mark.parametrize("interruption", (*EVIDENCE_RECOVERY_HISTORIES, "settlement_error"))
            def test_receipt_repairs_product_without_repeating_effect(self, interruption):
                assert examples.receipt_repair is not None
                with examples.environment(), _binding(examples.receipt_repair) as scenario:
                    assert_receipt_repairs_product(capability, scenario, interruption=interruption)

            cls.test_receipt_repairs_product_without_repeating_effect = with_fixtures(
                test_receipt_repairs_product_without_repeating_effect
            )

        if isinstance(capability, ObservedExternalOperation):

            @pytest.mark.django_db(transaction=True)
            @pytest.mark.parametrize("history", OBSERVATION_HISTORIES)
            def test_observation_recovers_without_resend(self, history):
                assert examples.observation is not None
                # Own clock/broker scope here so adopters need no fixture wiring.
                # Only provider controls and product observations come from them.
                with (
                    AsyncSingleThreadContext(),
                    frozen_scheduler_clock() as clock,
                    pytest.MonkeyPatch.context() as patcher,
                    accounted_background_work(patcher, clock) as driver,
                    examples.environment(),
                    hold_nudges(),
                    _binding(examples.observation) as scenario,
                ):
                    assert_observation_recovers_without_resend(capability, scenario, driver, history=history)

            cls.test_observation_recovers_without_resend = with_fixtures(test_observation_recovers_without_resend)

        if examples.producers:

            @pytest.mark.django_db(transaction=True)
            @pytest.mark.parametrize("producer", examples.producers)
            @pytest.mark.parametrize("rollback", [False, True], ids=["commit-lost-notification", "rollback"])
            def test_application_producer_transaction(self, producer: str, rollback: bool):
                # Each binding owns its boundary fakes. A suite-wide environment
                # may suppress nudges and hide notification-before-commit bugs.
                with _binding(examples.producers[producer]) as scenario:
                    assert_producer_transaction(capability, scenario, rollback=rollback)

            cls.test_application_producer_transaction = with_fixtures(test_application_producer_transaction)

        if examples.synchronous_callers:

            @pytest.mark.django_db(transaction=True)
            @pytest.mark.parametrize("caller", examples.synchronous_callers)
            @pytest.mark.parametrize("history", SYNCHRONOUS_CALLER_HISTORIES)
            def test_synchronous_caller(self, caller: str, history: str):
                # Like producers, each binding owns its boundary fakes: a suite-wide
                # nudge suppressor would hide a caller racing its own worker.
                with _binding(examples.synchronous_callers[caller]) as scenario:
                    assert_synchronous_caller(capability, scenario, history=history)

            cls.test_synchronous_caller = with_fixtures(test_synchronous_caller)

        if examples.admissions:

            @pytest.mark.django_db(transaction=True)
            @pytest.mark.parametrize("producer", examples.admissions)
            @pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
            def test_application_admission_transaction(self, producer: str, rollback: bool):
                # Delayed readiness must not be fabricated inside the real admission command.
                with _binding(examples.admissions[producer]) as scenario:
                    assert_producer_transaction(capability, scenario, rollback=rollback, recover=False)

            cls.test_application_admission_transaction = with_fixtures(test_application_admission_transaction)

        @pytest.mark.django_db(transaction=True)
        def test_lock_order_is_consistent(self):
            assert_lock_order_is_consistent(capability, examples)

        cls.test_lock_order_is_consistent = with_fixtures(test_lock_order_is_consistent)

        @pytest.mark.django_db(transaction=True)
        def test_two_workers_one_settlement(self):
            assert_two_workers_one_settlement(capability, examples)

        cls.test_two_workers_one_settlement = with_fixtures(test_two_workers_one_settlement)
        if isinstance(capability, OwnedDurableWorkCapability) and capability.discovery is not None:
            owned = capability
            edges = candidate_edges(owned)

            def candidate_edge_test(invariant: Callable[..., None]) -> Callable[..., None]:
                @pytest.mark.django_db(transaction=True)
                @pytest.mark.parametrize("edge", edges, ids=[edge.id for edge in edges])
                def test_candidate_edge(self, edge: CandidateEdge):
                    # Legacy rows reach discovery through the same query as valid ones;
                    # the derivation example supplies the valid owner each edge perturbs.
                    assert examples.derivation is not None
                    with examples.environment(), _binding(examples.derivation) as facts:
                        invariant(owned, facts.make_implied, edge)

                return with_fixtures(test_candidate_edge)

            if edges:
                cls.test_database_legal_candidate_is_admissible = candidate_edge_test(
                    assert_database_legal_candidate_is_admissible
                )
                cls.test_inadmissible_candidate_does_not_block_the_next = candidate_edge_test(
                    assert_inadmissible_candidate_does_not_block_the_next
                )

        @pytest.mark.django_db(transaction=True)
        @pytest.mark.parametrize("poison", [True, False], ids=["poisoned", "control"])
        @pytest.mark.parametrize("history", recovery_isolation_histories(capability))
        def test_one_poisoned_identity_does_not_stall_recovery(self, history: str, poison: bool):
            assert_one_poisoned_identity_does_not_stall_recovery(capability, examples, history=history, poison=poison)

        cls.test_one_poisoned_identity_does_not_stall_recovery = with_fixtures(
            test_one_poisoned_identity_does_not_stall_recovery
        )
        if capability.wakes:

            @pytest.mark.django_db(transaction=True)
            def test_settlement_wakes_declared_dependents(self):
                assert_settlement_wakes_declared_dependents(capability, examples.delivery, examples.environment)

            cls.test_settlement_wakes_declared_dependents = with_fixtures(test_settlement_wakes_declared_dependents)
        if capability.policy.max_rearms is not None:

            @pytest.mark.django_db(transaction=True)
            def test_terminal_failure_is_visible_and_rearmable(self):
                assert_terminal_failure_is_visible_and_rearmable(capability, examples)

            cls.test_terminal_failure_is_visible_and_rearmable = with_fixtures(
                test_terminal_failure_is_visible_and_rearmable
            )
        if not isinstance(capability, IndependentCompensation):
            # Compensation rows are born CLAIMED by their producer; losing that
            # worker is an execution loss, which legitimately spends a rearm.

            @pytest.mark.django_db(transaction=True)
            def test_lost_delivery_spends_no_retry_budget(self):
                assert_lost_delivery_spends_no_retry_budget(capability, examples)

            cls.test_lost_delivery_spends_no_retry_budget = with_fixtures(test_lost_delivery_spends_no_retry_budget)
        if isinstance(capability, DurableExternalOperation):
            assert examples.uncertainties is not None

            @pytest.mark.django_db(transaction=True)
            @pytest.mark.parametrize("factory", examples.uncertainties.values(), ids=examples.uncertainties.keys())
            def test_uncertainty_blocks_resend(self, factory):
                with examples.environment(), _binding(factory) as scenario:
                    assert_uncertainty_blocks_resend(capability, scenario)

            cls.test_uncertainty_blocks_resend = with_fixtures(test_uncertainty_blocks_resend)
            assert examples.rejections is not None

            @pytest.mark.django_db(transaction=True)
            @pytest.mark.parametrize("interruption", EVIDENCE_RECOVERY_HISTORIES)
            @pytest.mark.parametrize("factory", examples.rejections.values(), ids=examples.rejections.keys())
            def test_rejection_decision_survives_recovery(self, factory, interruption):
                with examples.environment():
                    assert_rejection_survives_recovery(capability, factory, interruption=interruption)

            cls.test_rejection_decision_survives_recovery = with_fixtures(test_rejection_decision_survives_recovery)
        if isinstance(capability, DurableScheduledAction):

            @pytest.mark.django_db(transaction=True)
            @pytest.mark.parametrize("worker_dies", [False, True], ids=["completed", "worker-death"])
            def test_cancellation_after_provider_entry(self, worker_dies):
                assert examples.snapshot is not None
                with (
                    examples.environment(),
                    hold_nudges(),
                    _binding(examples.snapshot) as scenario,
                ):
                    assert isinstance(scenario, CancellationExample), (
                        "scheduled examples must specify the completed outcome"
                    )
                    assert_cancellation_after_entry_is_honest(capability, scenario, worker_dies=worker_dies)

            if examples.fixtures:
                test_cancellation_after_provider_entry = pytest.mark.usefixtures(*examples.fixtures)(
                    test_cancellation_after_provider_entry
                )
            cls.test_cancellation_after_provider_entry = test_cancellation_after_provider_entry
        if isinstance(capability, DurableProjection):

            @pytest.mark.django_db(transaction=True)
            @pytest.mark.parametrize(
                ("worker_dies", "break_repair"),
                [(False, False), (True, False), (False, True)],
                ids=["late-receipt", "unknown-then-late-receipt", "rejects-missing-repair"],
            )
            def test_late_projection_write_is_repaired(self, worker_dies, break_repair):
                assert examples.snapshot is not None
                with (
                    examples.environment(),
                    hold_nudges(),
                    _binding(examples.snapshot) as scenario,
                ):
                    assert isinstance(scenario, ProjectionExample), (
                        "projection examples must specify the latest visible outcome"
                    )
                    original_write = capability.write_projection
                    writes = 0

                    def omit_repair(self, work):
                        nonlocal writes
                        writes += 1
                        # The newer and escaped older writes still land. Only
                        # subsequent repair writes are deliberately ineffective.
                        if writes <= 2:
                            original_write(work)

                    mutation = (
                        patch.object(type(capability), "write_projection", omit_repair)
                        if break_repair
                        else nullcontext()
                    )
                    expected = (
                        pytest.raises(AssertionError, match="recovery must restore the latest visible projection")
                        if break_repair
                        else nullcontext()
                    )
                    with mutation, expected:
                        assert_late_projection_write_is_repaired(capability, scenario, worker_dies=worker_dies)

            if examples.fixtures:
                test_late_projection_write_is_repaired = pytest.mark.usefixtures(*examples.fixtures)(
                    test_late_projection_write_is_repaired
                )
            cls.test_late_projection_write_is_repaired = test_late_projection_write_is_repaired
        if compensations:

            @pytest.mark.django_db(transaction=True)
            @pytest.mark.parametrize("break_cleanup", [False, True], ids=["compensated", "rejects-missing-cleanup"])
            def test_independent_compensation_after_primary_exhaustion(self, break_cleanup):
                assert examples.compensation is not None
                with (
                    examples.environment(),
                    hold_nudges(),
                    _binding(examples.compensation) as scenario,
                ):
                    mutation = (
                        patch.object(type(compensations[0]), "compensate", return_value=None)
                        if break_cleanup
                        else nullcontext()
                    )
                    expected = (
                        pytest.raises(AssertionError, match="independent cleanup must remove escaped output")
                        if break_cleanup
                        else nullcontext()
                    )
                    with mutation, expected:
                        assert_exhausted_producer_still_compensates(capability, compensations[0], scenario)

            if examples.fixtures:
                test_independent_compensation_after_primary_exhaustion = pytest.mark.usefixtures(*examples.fixtures)(
                    test_independent_compensation_after_primary_exhaustion
                )
            cls.test_independent_compensation_after_primary_exhaustion = (
                test_independent_compensation_after_primary_exhaustion
            )
        return cls

    return decorate
