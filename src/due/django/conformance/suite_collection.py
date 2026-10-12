"""Production-bound collection checks imported by enrolled suites only."""

from collections.abc import Mapping
from dataclasses import fields
from inspect import unwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from due.django.conformance.collected_effects import (
    CollectedEffect,
    ExampleSources,
    assert_collected_proofs,
    record_collected,
    source_location,
)
from due.django.runtime.names import RECOVERY_TASK
from due.pytest_plugin import requires_complete_suite

if TYPE_CHECKING:
    from due.django.conformance.examples import EffectExamples

from pytest_obligation.contract import (
    Claim,
    NotAssessed,
    ObligationContract,
    _covered_recovery_cases,
    contract_cases,
    contract_report,
    eligibility_bindings,
)

from due.django.capabilities.compensation import IndependentCompensation
from due.django.capabilities.observed_operations import ObservedExternalOperation
from due.django.capabilities.operations import DurableExternalOperation
from due.django.capabilities.projection import DurableProjection
from due.django.capabilities.scheduled import DurableScheduledAction
from due.django.conformance._internal.immutable_inputs import admission_paths
from due.django.conformance.composition import compensations_for
from due.django.conformance.discovery_admissibility import candidate_edges, related_candidate_edges
from due.django.conformance.observations import OBSERVATION_HISTORIES
from due.django.conformance.recovery_isolation import recovery_isolation_histories
from due.django.conformance.registrations import registered_capability, registration_of
from due.django.conformance.scenarios import (
    EVIDENCE_RECOVERY_HISTORIES,
    RECEIPT_RECOVERY_HISTORIES,
    SYNCHRONOUS_CALLER_HISTORIES,
    acknowledgement_replays,
)
from due.django.public import OwnedDurableWorkCapability


def example_sources(examples: EffectExamples, root: Path) -> ExampleSources:
    result: ExampleSources = {}
    # Reflect the canonical declarations, including empty named groups and
    # absent optional bindings. Do not keep a second field/requirement catalog.
    for field in fields(examples):
        value = getattr(examples, field.name)
        if field.name == "convergence_families":
            continue  # Reasoned decisions, not bindings; contract_report states them.
        if value is None:
            result[field.name] = None
        elif callable(value):
            result[field.name] = source_location(value, root)
        elif isinstance(value, Mapping):
            result[field.name] = {name: source_location(factory, root) for name, factory in value.items()}
        elif field.name == "process_handoffs":
            result[field.name] = {history.name: source_location(history.run, root) for history in value}
        elif field.name == "handoffs":
            result[field.name] = {history.name: source_location(history.transition, root) for history in value}
    return result


def collect_suite(config: pytest.Config, collected: list[pytest.Function]) -> str:
    cls = collected[0].cls
    assert cls is not None
    reasons = {
        "A": "registered scheduled recovery",
        "B": "lease-based execution",
        "C": "provider operation with durable attempt evidence",
        "D": "work or provider receipt pruning",
        "E": "moving desired state, cancelable scheduled action, or bound result application or interleaving",
        "F": "registered canonical discovery; also requires A+F composition",
        "G": "production execution gate; also requires A+G recovery composition",
        "H": "replay-safe production effect",
        "I": "standalone command partial-admission fault binding",
        "J": "finite production retry budget",
    }
    capability = cls.__durable_work_capability__
    entry = registration_of(capability.model)
    assert registered_capability(entry.name) is capability, (
        f"{cls.__name__}: suite substituted its registered capability"
    )
    contract = cls.__due_work_contract__
    assert isinstance(contract, ObligationContract)
    assert (contract.eligibility is not None) == (capability.eligibility is not None), (
        f"{cls.__name__}: eligibility proof declaration differs from production capability"
    )
    cases = contract_cases(contract)
    cases += _covered_recovery_cases(contract, cls.__due_work_sources__)
    expected = {f"test_due_work_contract[{case.values[0].id}]" for case in cases}
    expected |= {
        "test_registered_producer_lost_notification",
        "test_registered_effect_enrollment",
        "test_migrated_work_indexes",
        "test_lock_order_is_consistent",
        "test_two_workers_one_settlement",
    }
    for history in recovery_isolation_histories(capability):
        for outcome in ("poisoned", "control"):
            expected.add(f"test_one_poisoned_identity_does_not_stall_recovery[{history}-{outcome}]")
    examples = cls.__durable_work_examples__
    assert tuple(
        history.model_copy(update={"arrange": unwrap(history.arrange)}) for history in contract.handoffs
    ) == tuple(history.model_copy(update={"arrange": unwrap(history.arrange)}) for history in examples.handoffs), (
        f"{cls.__name__}: handoff histories differ from declared product transitions"
    )
    assert set(contract.completions) == set(examples.completions), "terminal completion bindings differ"
    assert set(contract.delivery_replays) == set(examples.delivery_replays), "database delivery replay bindings differ"
    assert contract.process_handoffs == examples.process_handoffs, (
        f"{cls.__name__}: process histories differ from declared product transitions"
    )
    assert set(contract.admission) == set(examples.admission_failures), (
        f"{cls.__name__}: admission atomicity variants differ from declared product scenarios"
    )
    assert (contract.convergence is None) == (examples.convergence is None), (
        f"{cls.__name__}: result-application binding differs from the declared example"
    )
    assert contract.convergence_families == examples.convergence_families, (
        f"{cls.__name__}: E family decisions differ from the declared examples"
    )
    gate_names = set()
    if isinstance(examples.eligibility, Mapping):
        gate_names = set(examples.eligibility)
    elif examples.eligibility is not None:
        gate_names = {""}
    assert set(eligibility_bindings(contract)) == gate_names, (
        f"{cls.__name__}: eligibility variants differ from declared product scenarios"
    )
    if isinstance(capability, DurableExternalOperation):
        for history in RECEIPT_RECOVERY_HISTORIES:
            expected.add(f"test_receipt_repairs_product_without_repeating_effect[{history}]")
        for uncertainty in examples.uncertainties:
            expected.add(f"test_uncertainty_blocks_resend[{uncertainty}]")
        for rejection in examples.rejections:
            for history in EVIDENCE_RECOVERY_HISTORIES:
                expected.add(f"test_rejection_decision_survives_recovery[{rejection}-{history}]")
    if isinstance(capability, ObservedExternalOperation):
        for history in OBSERVATION_HISTORIES:
            expected.add(f"test_observation_recovers_without_resend[{history}]")
    for caller in examples.synchronous_callers:
        for history in SYNCHRONOUS_CALLER_HISTORIES:
            expected.add(f"test_synchronous_caller[{history}-{caller}]")
    for producer in examples.producers:
        for outcome in ("commit-lost-notification", "rollback"):
            expected.add(f"test_application_producer_transaction[{outcome}-{producer}]")
    for producer in examples.admissions:
        for outcome in ("commit", "rollback"):
            expected.add(f"test_application_admission_transaction[{outcome}-{producer}]")
    if examples.perturb_inputs is not None:
        for path in admission_paths(examples):
            expected.add(f"test_immutable_inputs_hold[{path.id}]")
    if isinstance(capability, OwnedDurableWorkCapability) and capability.discovery is not None:
        # Unbounded discovery claims every eligible owner, so its generated completeness proof cannot disappear.
        if capability.discovery.horizon is None:
            expected.add("test_discovery_selection_is_complete")
        edge_ids = [edge.id for edge in candidate_edges(capability)]
        edge_ids += [related.id for related in related_candidate_edges(examples.discovery_edges)]
        for edge_id in edge_ids:
            expected.add(f"test_database_legal_candidate_is_admissible[{edge_id}]")
            expected.add(f"test_inadmissible_candidate_does_not_block_the_next[{edge_id}]")
        if capability.discovery.horizon is None:
            expected.add("test_discovery_selection_is_complete")
    if capability.policy.max_rearms is not None:
        expected.add("test_terminal_failure_is_visible_and_rearmable")
    if capability.wakes:
        expected.add("test_settlement_wakes_declared_dependents")
    if not isinstance(capability, IndependentCompensation):
        expected.add("test_lost_delivery_spends_no_retry_budget")
    if acknowledgement_replays(capability):
        expected.add("test_lost_acknowledgement_replay_converges")
    if compensations_for(capability):
        expected |= {
            "test_independent_compensation_after_primary_exhaustion[compensated]",
            "test_independent_compensation_after_primary_exhaustion[rejects-missing-cleanup]",
        }
    if isinstance(capability, DurableProjection):
        expected |= {
            "test_late_projection_write_is_repaired[late-receipt]",
            "test_late_projection_write_is_repaired[unknown-then-late-receipt]",
            "test_late_projection_write_is_repaired[rejects-missing-repair]",
        }
    if isinstance(capability, DurableScheduledAction):
        expected |= {
            "test_cancellation_after_provider_entry[completed]",
            "test_cancellation_after_provider_entry[worker-death]",
        }
    actual = {item.name for item in collected}
    if requires_complete_suite(config.args, collected[0].path.resolve(), cls.__name__):
        assert_collected_proofs(expected, actual, cls.__name__)
    report = CollectedEffect(
        capability=entry.model._meta.label_lower,
        suite=f"{cls.__module__}.{cls.__name__}",
        contract=contract_report(contract),
        production_policy={
            "execution_mode": type(capability).__qualname__,
            "recovery_task": RECOVERY_TASK,
            "recovery_args": repr((entry.name,)),
            "queue": entry.queue,
            "cadence_seconds": str(entry.cadence_seconds),
            "execution_time_limit": str(entry.execution_time_limit),
            "execution_soft_time_limit": str(entry.execution_soft_time_limit),
            "policy": repr(capability.policy),
            "eligibility": "database" if capability.eligibility is not None else "unrestricted",
            "eligibility_scenarios": ", ".join(name or "default" for name in sorted(gate_names)),
            "eligibility_recheck_after": str(capability.eligibility.recheck_after)
            if capability.eligibility is not None
            else "None",
            "compensation": ", ".join(type(item).__qualname__ for item in compensations_for(capability)),
        },
        required_by={
            profile.name: reasons[profile.name]
            for profile, disposition in contract.profiles.items()
            if isinstance(disposition, Claim)
        },
        expected=sorted(expected),
        unassessed=[
            f"test_due_work_contract[{parameter.values[0].id}]"
            for parameter in cases
            if isinstance(parameter.values[0].assessment, NotAssessed)
        ],
        collected=sorted(actual),
        selected=[],
        outcomes={},
        bindings=example_sources(examples, config.rootpath),
        proof_sources={
            **{item.name: source_location(item.obj, config.rootpath) for item in collected},
            **{
                f"test_due_work_contract[{case.values[0].id}]": source_location(case.values[0].run, config.rootpath)
                for case in cases
            },
        },
    )
    return record_collected(config, entry.name, report, collected)
