"""Compile application examples into shared contracts and Due-specific proofs.

Owned by the public ``conformance.suite`` facade. Keep fault execution in the
shared pytest-obligation runners and production behavior in Due's runtime;
this module only binds them and adds work-table assertions and collection marks.
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext, suppress
from datetime import timedelta
from functools import wraps
from typing import Any
from unittest.mock import patch

import pytest
from asgiref.sync import AsyncSingleThreadContext
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
from pytest_obligation.evidence.observation import assert_observation
from pytest_obligation.integrations.django.edge_values import CandidateEdge
from pytest_obligation.integrations.django.lock_order import recording_against_the_ledger
from pytest_obligation.integrations.django.selection_completeness import (
    CompletenessBinding,
    assert_selection_is_complete,
)
from pytest_obligation.integrations.django.writes import (
    execute_reporting_autocommit_write,
    row_write,
)
from pytest_obligation.profiles.crash_ambiguity import AmbiguityAware
from pytest_obligation.profiles.durable_retention import Retention
from pytest_obligation.profiles.eventual_convergence import (
    SupersededSnapshot,
)
from pytest_obligation.profiles.fact_derived_obligations import StateDerived
from pytest_obligation.profiles.gated_execution import (
    ExecutionGate,
    ExecutionGateBinding,
)
from pytest_obligation.profiles.indivisible_admission import AdmissionAtomicity
from pytest_obligation.profiles.job_retry_limits import BoundedRetry, ClassifiedFailure, FailureClass, PersistentRetry

from due.django.capabilities.authoring import FAILED_TERMINAL_STATES
from due.django.capabilities.authoring import ReplaySafeEffect as ReplaySafeCapability
from due.django.capabilities.compensation import IndependentCompensation
from due.django.capabilities.observed_operations import ObservedExternalOperation
from due.django.capabilities.operations import DurableExternalOperation
from due.django.capabilities.projection import DurableProjection
from due.django.capabilities.scheduled import DurableScheduledAction
from due.django.conformance._internal.immutable_inputs import (
    AdmissionPath,
    admission_paths,
    assert_immutable_inputs_hold,
    checks_immutable_inputs,
)
from due.django.conformance.background import (
    accounted_background_work,
    frozen_scheduler_clock,
    harness_owned_background_work,
)
from due.django.conformance.binding import Binding
from due.django.conformance.binding import entered_binding as _binding
from due.django.conformance.bindings import (
    DurableWorkBindings,
    assert_registered_worker_options,
)
from due.django.conformance.composition import (
    CancellationExample,
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
    discovery_completeness,
)
from due.django.conformance.examples import (
    DeliveryExample,
    EffectExamples,
    EligibilityExample,
    FactExamples,
    FailureExample,
    RejectionExample,
)
from due.django.conformance.indexes import assert_work_indexes
from due.django.conformance.observations import (
    OBSERVATION_HISTORIES,
    assert_observation_recovers_without_resend,
)
from due.django.conformance.recovery_isolation import (
    assert_one_poisoned_identity_does_not_stall_recovery,
    recovery_isolation_histories,
)
from due.django.conformance.registrations import durable_work_installed, registered_capability
from due.django.conformance.scenarios import (
    EVIDENCE_RECOVERY_HISTORIES,
    RECEIPT_RECOVERY_HISTORIES,
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
from due.django.models import TERMINAL_WORK_STATES, EffectAttempt, EffectAttemptOutcome
from due.django.public import (
    DatabaseEffect,
    DurableWork,
    DurableWorkCapability,
    DurableWorkState,
    OwnedDurableWorkCapability,
    UniverseDurableWork,
    database_now,
)
from due.django.runtime.coordination import outstanding_work
from due.django.runtime.registry import DurableWorkRegistration
from due.django.runtime.tasks import (
    recover_durable_work_task,
    run_durable_work_task,
)
from due.django.testing import expire_lease, hold_nudges, make_due
from due.errors import WorkFailureDisposition


def assert_registered_schedule(entry: DurableWorkRegistration) -> None:
    """Inspect installed configuration, never the schedule generator's return value."""
    from celery import current_app as celery_app

    from due.django.runtime.diagnostics import schedule_errors

    errors = schedule_errors(entry, celery_app)
    assert not errors, "\n".join(errors)
    capability = registered_capability(entry.name)
    assert capability.model is entry.model


def _in_environment[T](examples: EffectExamples, factory: Binding[T]) -> Binding[T]:
    @contextmanager
    @wraps(factory)
    def bound():
        with _binding(examples.environment), _binding(factory) as value:
            yield value

    return bound


@contextmanager
def fact_bindings[OwnerT: models.Model, W: UniverseDurableWork, ResultT](
    capability: OwnedDurableWorkCapability[OwnerT, W, ResultT], factory: Binding[FactExamples[W]]
) -> Iterator[StateDerived]:
    """Bind canonical product facts to the shared state-derived obligation proof.

    The factory owns arrangement; the capability owns derive and execution. The
    yielded binding remains inside the factory scope through all proof operations."""
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
    """Replace a provider seam with a counted OSError.

    ``path`` is the patch target production calls, whatever its arguments (a
    work row, a source key, a request). The context yields a retry example whose
    counter observes every provider entry and restores the seam on exit. The
    retry proofs drive one work at a time, so every entry belongs to it."""
    calls = 0

    def fail(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise OSError("injected provider outage")

    with patch(path, fail):
        yield FailureExample(attempts=lambda work: calls)


@contextmanager
def retry_bindings[W: UniverseDurableWork, ResultT](
    bindings: DurableWorkBindings[W, ResultT], factory: Binding[FailureExample[W]]
) -> Iterator[BoundedRetry]:
    """Bind a finite production retry policy to the shared BoundedRetry proof.

    The failure factory remains open while the proof drives actual executions;
    attempt counts come from the independent example, not inferred work state."""
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
def persistent_retry_bindings[W: UniverseDurableWork, ResultT](
    bindings: DurableWorkBindings[W, ResultT], factory: Binding[FailureExample[W]]
) -> Iterator[PersistentRetry]:
    """Bind an unbounded production retry policy to the shared PersistentRetry proof.

    The same failure example a bounded policy exhausts must, with no budget,
    leave the work owed: never terminal, and due again once its delay elapses."""
    capability = bindings.capability
    assert capability.policy.max_rearms is None
    with _binding(factory) as failure:

        def make():
            work = bindings.make()
            failure.prepare(work)
            return work

        def execute(work):
            with suppress(Exception):
                capability.attempt_execution(work.pk)

        yield PersistentRetry(
            name=bindings.registration.name,
            make_failing=make,
            due_work=lambda: capability.due_work(),
            run_once=execute,
            advance_to_due=bindings.age,
            is_terminal=lambda work: bindings.observe(work)[0] in TERMINAL_WORK_STATES,
            failure_attempt_count=failure.attempts,
            observe=bindings.observe,
            identity_of=lambda work: work.pk,
            # A zero retry delay makes the next due pass the retry; any positive one forbids a hot loop.
            waits=capability.policy.delay_at(0) > timedelta(0),
        )


@contextmanager
def classified_failure_bindings[W: UniverseDurableWork, ResultT](
    bindings: DurableWorkBindings[W, ResultT], factory: Binding[RejectionExample[W, Any]], *, outcome: str
) -> Iterator[ClassifiedFailure]:
    """Bind one named provider rejection to the shared outcome-classification proof.

    The example admits work whose provider will reject it, the disposition the
    application intends, and the product outcome that rejection must leave.
    Production classifies the failure; the proof checks it took the declared class."""
    capability = bindings.capability
    with hold_nudges(), _binding(factory) as scenario:
        name = f"{bindings.registration.name} {outcome!r}"
        if scenario.disposition is WorkFailureDisposition.UNKNOWN:
            raise ObligationContractDesignError(
                f"{name}: an UNKNOWN outcome needs provider observation to resolve, which only an observed "
                f"external operation has. Classify it RETRYABLE or TERMINAL, or use that mode"
            )
        if scenario.expected_attempts != 1:
            raise ObligationContractDesignError(
                f"{name}: a classified rejection is one provider call per execution; "
                f"expected_attempts={scenario.expected_attempts} describes a different shape"
            )
        if scenario.disposition is WorkFailureDisposition.RETRYABLE and capability.policy.max_rearms == 0:
            raise ObligationContractDesignError(
                f"{name}: max_rearms=0 exhausts on the first failure, so nothing is retryable"
            )
        delivery = scenario.delivery
        effect_calls = delivery.effect_calls
        assert effect_calls is not None, (
            f"{name}: a classified rejection requires an independent provider-call observation"
        )

        def make():
            bindings.age(delivery.work)
            delivery.work.refresh_from_db()
            return delivery.work

        def execute(work):
            with suppress(Exception):
                capability.attempt_execution(work.pk)

        yield ClassifiedFailure(
            name=name,
            classification=FailureClass(scenario.disposition.value),
            make_failing=make,
            due_work=lambda: capability.due_work(),
            run_once=execute,
            advance_to_due=bindings.age,
            is_terminal=lambda work: bindings.observe(work)[0] in TERMINAL_WORK_STATES,
            failure_attempt_count=lambda work: effect_calls(),
            observe=bindings.observe,
            identity_of=lambda work: work.pk,
            waits=capability.policy.delay_at(0) > timedelta(0),
            product_outcome=lambda work: delivery.observe(),
            expected_product_outcome=lambda work: delivery.expected,
        )


@contextmanager
def snapshot_bindings[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT], factory: Binding[SupersessionExample[W, Any]]
) -> Iterator[SupersededSnapshot]:
    """Capture a real queued invocation, supersede intent, then replay its old arguments.

    The example owns the product change and observation; this adapter captures
    the worker payload and binds the shared SupersededSnapshot oracle."""
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
    """Bind projection evidence pruning while preserving unresolved attempt records."""
    capability = bindings.capability
    assert isinstance(capability, DurableProjection)

    def attempt(resolved=False):
        work = bindings.make()
        claim = bindings.claim(work)
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
    """Bind an external operation to uncertainty, receipt repair and retention proofs.

    Application examples supply canonical applied evidence; the production
    capability owns attempt entry, ambiguity handling and local settlement."""
    capability = bindings.capability
    assert isinstance(capability, DurableExternalOperation)
    claims, attempts = {}, {}
    model = capability.model

    def claim():
        work = bindings.make()
        claimed = bindings.claim(work)
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
        expire_lease(model.objects.get(pk=pk))

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
    if issubclass(mode, DatabaseEffect):
        return (*common, "delivery_replays")
    if issubclass(mode, DurableExternalOperation):
        required = (*common, "applied_receipt", "receipt_repair", "rejections", "uncertainties")
        return (*required, "observation") if issubclass(mode, ObservedExternalOperation) else required
    if issubclass(mode, (DurableProjection, DurableScheduledAction)):
        return (*common, "replay", "snapshot")
    if issubclass(mode, (IndependentCompensation, ReplaySafeCapability, OwnedDurableWorkCapability)):
        return (*common, "replay")
    raise ObligationContractDesignError(f"unrecognized capability mode: {mode.__name__}")


def _convergence_decline_defects(
    capability: DurableWorkCapability[Any, Any], examples: EffectExamples[Any], *, convergent: bool, name: str
) -> list[str]:
    """Refuse a contradictory profile E decision; return the declarations still missing."""
    declared = [
        field
        for field in ("immutable_inputs", "perturb_inputs", "convergence_not_assessed")
        if getattr(examples, field)
    ]
    if convergent:
        if declared:
            raise ObligationContractDesignError(
                f"{name}: profile E is claimed (moving input or a bound E family), so {', '.join(declared)} "
                f"decide(s) nothing; remove them"
            )
        return []
    if examples.immutable_inputs and examples.convergence_not_assessed:
        raise ObligationContractDesignError(
            f"{name}: immutable_inputs says admitted inputs never move; convergence_not_assessed says they do"
        )
    for decision in ("immutable_inputs", "convergence_not_assessed"):
        if getattr(examples, decision) is not None and not is_real_reason(getattr(examples, decision)):
            raise ObligationContractDesignError(f"{name}: {decision} must state the domain rule, not a placeholder")
    checked = checks_immutable_inputs(capability)
    if examples.perturb_inputs is not None and not (checked and examples.immutable_inputs):
        raise ObligationContractDesignError(
            f"{name}: perturb_inputs checks immutable_inputs on owned replay-safe work only; "
            + (
                "this capability cannot move admitted inputs (an evidence-required operation refuses new input "
                "and supersession; an independent compensation has no admission seam), so there is nothing to perturb"
                if not checked
                else "it is declared without immutable_inputs"
            )
        )
    if not (examples.immutable_inputs or examples.convergence_not_assessed):
        return [
            "immutable_inputs (the domain rule why admitted inputs never move) or convergence_not_assessed "
            "(which production path moves them)"
        ]
    if not (checked and examples.immutable_inputs):
        return []
    if not admission_paths(examples):
        raise ObligationContractDesignError(
            f"{name}: immutable_inputs is checked by re-running a real admission path; bind producers, admissions, "
            f"admission_failures or synchronous_callers"
        )
    if examples.perturb_inputs is None:
        return ["perturb_inputs (immutable_inputs is checked: move the facts an admitted work's inputs derive from)"]
    return []


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
        # Only a bounded policy exhausts; the terminal example observes that exhaustion.
        "terminal": capability.policy.max_rearms is not None,
        "snapshot": moving,
        "derivation": discovery,
        "compensation": bool(compensations_for(capability)),
        "eligibility": capability.eligibility is not None,
    }
    missing = [field for field, needed in required.items() if needed and not getattr(examples, field)]
    convergent = moving or bool(examples.in_flight or examples.evidence_confluence) or examples.convergence is not None
    missing.extend(_convergence_decline_defects(capability, examples, convergent=convergent, name=name))
    if missing:
        raise ObligationContractDesignError(f"{name}: production semantics require examples: {', '.join(missing)}")
    for scenario_name, scenario_bindings in (
        ("rejections", examples.rejections),
        ("uncertainties", examples.uncertainties),
    ):
        if scenario_bindings is not None and not scenario_bindings:
            raise ObligationContractDesignError(f"{name}: {scenario_name} needs at least one real provider scenario")
    # Every mode may declare its rejection matrix (profile J's classified failures);
    # only an operation records uncertainty evidence it can later resolve.
    if not operation and examples.uncertainties is not None:
        raise ObligationContractDesignError(f"{name}: uncertainties examples require the operation execution mode")
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

    # One failure example drives either half of profile J: a bounded policy
    # must exhaust at its budget, an unbounded one must keep the work owed.
    def retry():
        assert examples.retry is not None, "validated: every policy declares retry"
        # ARRANGE: domain examples provide genuine work for retry executor.
        # REAL PRODUCTION: the retry executor binding calls this registered capability.
        # EXTERNAL SEAM: only the example's provider/fault seam is substituted.
        # OBSERVE: independent product observations and persisted work/evidence.
        return retry_bindings(bindings, examples.retry)

    def persistent_retry():
        assert examples.retry is not None, "validated: every policy declares retry"
        # ARRANGE: domain examples provide genuine work for the unbounded retry executor.
        # REAL PRODUCTION: the retry executor binding calls this registered capability.
        # EXTERNAL SEAM: only the example's provider/fault seam is substituted.
        # OBSERVE: provider attempts and persisted work state, which must stay owed.
        return persistent_retry_bindings(bindings, examples.retry)

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

    def classified_binding(outcome: str, factory: Binding[RejectionExample[W, Any]]) -> Binding[ClassifiedFailure]:
        def classified():
            # ARRANGE: the domain example admits work its provider will reject.
            # REAL PRODUCTION: the registered capability executes and classifies the failure.
            # EXTERNAL SEAM: only the example's provider rejection is scripted.
            # OBSERVE: provider calls, persisted work state and the example's product outcome.
            return classified_failure_bindings(bindings, factory, outcome=outcome)

        return classified

    # An operation proves its rejections through recorded evidence instead
    # (test_rejection_decision_survives_recovery); every other mode routes them here.
    classified_failures = {
        outcome: _in_environment(examples, classified_binding(outcome, factory))
        for outcome, factory in (examples.rejections or {}).items()
        if not operation
    }

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
            Profile.E: Claim()
            if convergent
            else NotAssessed(because=examples.convergence_not_assessed)
            if examples.convergence_not_assessed
            else NotApplicable(examples.immutable_inputs or ""),
            Profile.F: Claim() if discovery else NotApplicable("No canonical discovery is registered."),
            Profile.HARMLESS_REPLAY: Decline(
                "Production requires definite provider evidence; another call can duplicate an occurrence."
            )
            if operation
            else NotApplicable(
                "DatabaseEffect commits its callback with the work fence; the separate delivery replay proof checks that boundary, not raw external-call idempotency."
            )
            if isinstance(capability, DatabaseEffect)
            else Claim(),
            # Bounded: exhaustion at the budget. Unbounded: the obligation stays owed and paced.
            Profile.JOB_RETRY_LIMITS: Claim(),
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
        handoffs=tuple(
            history.model_copy(update={"arrange": _in_environment(examples, history.arrange)})
            for history in examples.handoffs
        ),
        process_handoffs=examples.process_handoffs,
        handoff_delivery=WorkTableDelivery(capability) if examples.handoffs else None,
        replay=bound(examples.replay),
        completions={name: bound(factory) for name, factory in examples.completions.items()},
        delivery_replays={name: bound(factory) for name, factory in examples.delivery_replays.items()},
        retry=bound(retry) if capability.policy.max_rearms is not None else None,
        persistent_retry=bound(persistent_retry) if capability.policy.max_rearms is None else None,
        classified_failures=classified_failures,
    )
    return contract


@contextmanager
def eligibility_binding[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT],
    examples: EffectExamples[W],
    factory: Binding[EligibilityExample[W, Any]],
) -> Iterator[ExecutionGate]:
    """Bind a real readiness transition to the shared execution-gate histories.

    The production binding guard rejects test-authored readiness decisions. The
    accounted driver runs actual messages through blocked and eligible phases."""
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
        frozen_scheduler_clock() as clock,
        harness_owned_background_work(clock) as driver,
        _binding(examples.environment),
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
    """Drop the producer wakeup and require recovery to reach its independent product oracle."""
    bindings = DurableWorkBindings(capability, examples.make_work)
    entry = bindings.registration
    assert_registered_schedule(entry)
    sent = []

    def capture(*, args, **options):
        if args[0] == entry.name:
            sent.append((args, options))

    with (
        _binding(examples.environment),
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
        _binding(examples.environment),
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
        _binding(examples.environment),
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
        expire_lease(work)
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
    assert examples.retry is not None, "validated: bounded policies declare retry"
    with (
        _binding(examples.environment),
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
    with _binding(environment), hold_nudges() as held, _binding(delivery) as example:
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
    """Enroll a pytest class with shared profiles and work-table-specific proofs.

    ``capability`` is the registered production instance. ``examples`` supplies
    scoped application evidence; ``covers`` names production handoff sources for
    static inventory. Required cases follow the mode and policy, not a caller
    profile list. Collection records origins and rejects missing selected proofs.
    Configure the host and Django before declaring the suite."""
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

        def proof[**P](test: Callable[P, None], *, name: str | None = None) -> Callable[P, None]:
            """Install each database proof with the same transactional and adopter fixtures."""
            test = pytest.mark.django_db(transaction=True)(test)
            if examples.fixtures:
                test = pytest.mark.usefixtures(*examples.fixtures)(test)
            setattr(cls, name if name is not None else test.__name__, test)
            return test

        @proof
        def test_registered_producer_lost_notification(self):
            assert_lost_notification_reaches_domain_outcome(capability, examples)

        def test_registered_effect_enrollment(self):
            assert registered_capability(entry.name) is capability
            assert_registered_schedule(entry)

        cls.test_registered_effect_enrollment = test_registered_effect_enrollment

        @proof
        def test_migrated_work_indexes(self):
            assert_work_indexes(capability.model)

        if isinstance(capability, DurableExternalOperation):

            @proof
            @pytest.mark.parametrize("interruption", RECEIPT_RECOVERY_HISTORIES)
            def test_receipt_repairs_product_without_repeating_effect(self, interruption):
                assert examples.receipt_repair is not None
                with _binding(examples.environment), _binding(examples.receipt_repair) as scenario:
                    assert_receipt_repairs_product(capability, scenario, interruption=interruption)

        if isinstance(capability, ObservedExternalOperation):

            @proof
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
                    _binding(examples.environment),
                    hold_nudges(),
                    _binding(examples.observation) as scenario,
                ):
                    assert_observation_recovers_without_resend(capability, scenario, driver, history=history)

        if examples.producers:

            @proof
            @pytest.mark.parametrize("producer", examples.producers)
            @pytest.mark.parametrize("rollback", [False, True], ids=["commit-lost-notification", "rollback"])
            def test_application_producer_transaction(self, producer: str, rollback: bool):
                # Each binding owns its boundary fakes. A suite-wide environment
                # may suppress nudges and hide notification-before-commit bugs.
                with _binding(examples.producers[producer]) as scenario:
                    assert_producer_transaction(capability, scenario, rollback=rollback)

        if examples.synchronous_callers:

            @proof
            @pytest.mark.parametrize("caller", examples.synchronous_callers)
            @pytest.mark.parametrize("history", SYNCHRONOUS_CALLER_HISTORIES)
            def test_synchronous_caller(self, caller: str, history: str):
                # Like producers, each binding owns its boundary fakes: a suite-wide
                # nudge suppressor would hide a caller racing its own worker.
                with _binding(examples.synchronous_callers[caller]) as scenario:
                    assert_synchronous_caller(capability, scenario, history=history)

        if examples.perturb_inputs is not None:
            assert isinstance(capability, OwnedDurableWorkCapability)
            owned = capability
            paths = admission_paths(examples)

            @proof
            @pytest.mark.parametrize("path", paths, ids=[path.id for path in paths])
            def test_immutable_inputs_hold(self, path: AdmissionPath):
                # Each admission binding owns its boundary fakes, as in the producer proofs.
                assert_immutable_inputs_hold(owned, examples, path, name=entry.name)

        if examples.admissions:

            @proof
            @pytest.mark.parametrize("producer", examples.admissions)
            @pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
            def test_application_admission_transaction(self, producer: str, rollback: bool):
                # Delayed readiness must not be fabricated inside the real admission command.
                with _binding(examples.admissions[producer]) as scenario:
                    assert_producer_transaction(capability, scenario, rollback=rollback, recover=False)

        @proof
        def test_lock_order_is_consistent(self):
            assert_lock_order_is_consistent(capability, examples)

        @proof
        def test_two_workers_one_settlement(self):
            assert_two_workers_one_settlement(capability, examples)

        if isinstance(capability, OwnedDurableWorkCapability) and capability.discovery is not None:
            owned = capability
            edges = candidate_edges(owned)

            def candidate_edge_test(name: str, invariant: Callable[..., None]) -> None:
                @pytest.mark.parametrize("edge", edges, ids=[edge.id for edge in edges])
                def test_candidate_edge(self, edge: CandidateEdge):
                    # Legacy rows reach discovery through the same query as valid ones;
                    # the derivation example supplies the valid owner each edge perturbs.
                    assert examples.derivation is not None
                    with _binding(examples.environment), _binding(examples.derivation) as facts:
                        invariant(owned, facts.make_implied, edge)

                proof(test_candidate_edge, name=name)

            if edges:
                candidate_edge_test(
                    "test_database_legal_candidate_is_admissible", assert_database_legal_candidate_is_admissible
                )
                candidate_edge_test(
                    "test_inadmissible_candidate_does_not_block_the_next",
                    assert_inadmissible_candidate_does_not_block_the_next,
                )

            if owned.discovery is not None and owned.discovery.horizon is None:
                # Declared unbounded: the candidate query claims to be every owner still owed.

                @contextmanager
                def discovered() -> Iterator[CompletenessBinding]:
                    # Entered once per generated owner, so no fake or row outlives its example.
                    assert examples.derivation is not None
                    with _binding(examples.environment), _binding(examples.derivation) as facts:
                        yield discovery_completeness(owned, facts.make_implied)

                @proof
                def test_discovery_selection_is_complete(self, due_work_selection_budget: int):
                    # An owner the candidate query skips must owe no work to the derivation step.
                    assert_selection_is_complete(discovered)

        @proof
        @pytest.mark.parametrize("poison", [True, False], ids=["poisoned", "control"])
        @pytest.mark.parametrize("history", recovery_isolation_histories(capability))
        def test_one_poisoned_identity_does_not_stall_recovery(self, history: str, poison: bool):
            assert_one_poisoned_identity_does_not_stall_recovery(capability, examples, history=history, poison=poison)

        if capability.wakes:

            @proof
            def test_settlement_wakes_declared_dependents(self):
                assert_settlement_wakes_declared_dependents(capability, examples.delivery, examples.environment)

        if capability.policy.max_rearms is not None:

            @proof
            def test_terminal_failure_is_visible_and_rearmable(self):
                assert_terminal_failure_is_visible_and_rearmable(capability, examples)

        if not isinstance(capability, IndependentCompensation):
            # Compensation rows are born CLAIMED by their producer; losing that
            # worker is an execution loss, which legitimately spends a rearm.

            @proof
            def test_lost_delivery_spends_no_retry_budget(self):
                assert_lost_delivery_spends_no_retry_budget(capability, examples)

        if isinstance(capability, DurableExternalOperation):
            assert examples.uncertainties is not None

            @proof
            @pytest.mark.parametrize("factory", examples.uncertainties.values(), ids=examples.uncertainties.keys())
            def test_uncertainty_blocks_resend(self, factory):
                with _binding(examples.environment), _binding(factory) as scenario:
                    assert_uncertainty_blocks_resend(capability, scenario)

            assert examples.rejections is not None

            @proof
            @pytest.mark.parametrize("interruption", EVIDENCE_RECOVERY_HISTORIES)
            @pytest.mark.parametrize("factory", examples.rejections.values(), ids=examples.rejections.keys())
            def test_rejection_decision_survives_recovery(self, factory, interruption):
                with _binding(examples.environment):
                    assert_rejection_survives_recovery(capability, factory, interruption=interruption)

        if isinstance(capability, DurableScheduledAction):

            @proof
            @pytest.mark.parametrize("worker_dies", [False, True], ids=["completed", "worker-death"])
            def test_cancellation_after_provider_entry(self, worker_dies):
                assert examples.snapshot is not None
                with (
                    _binding(examples.environment),
                    hold_nudges(),
                    _binding(examples.snapshot) as scenario,
                ):
                    assert isinstance(scenario, CancellationExample), (
                        "scheduled examples must specify the completed outcome"
                    )
                    assert_cancellation_after_entry_is_honest(capability, scenario, worker_dies=worker_dies)

        if isinstance(capability, DurableProjection):

            @proof
            @pytest.mark.parametrize(
                ("worker_dies", "break_repair"),
                [(False, False), (True, False), (False, True)],
                ids=["late-receipt", "unknown-then-late-receipt", "rejects-missing-repair"],
            )
            def test_late_projection_write_is_repaired(self, worker_dies, break_repair):
                assert examples.snapshot is not None
                with (
                    _binding(examples.environment),
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

        if compensations:

            @proof
            @pytest.mark.parametrize("break_cleanup", [False, True], ids=["compensated", "rejects-missing-cleanup"])
            def test_independent_compensation_after_primary_exhaustion(self, break_cleanup):
                assert examples.compensation is not None
                with (
                    _binding(examples.environment),
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

        return cls

    return decorate
