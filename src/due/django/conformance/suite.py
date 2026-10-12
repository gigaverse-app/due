"""Public entry points for application-bound work-table conformance.

Enroll a test class with ``effect_contract_suite(capability, examples=...)``.
The production capability determines required proofs; ``EffectExamples`` supplies
commands, scoped provider seams and independent product observations. Missing
assessments remain visible in reports and can fail CI.

Example declarations live in ``conformance.examples``; they remain re-exported
here for existing adopters. Advanced contract builders and individual assertions
also retain their imports. Suite generation lives in ``conformance._internal``
and is not an application extension point. See ``docs/conformance.md`` and
``docs/conformance-api.md`` for the workflow and API contracts.
"""

from due.django.conformance._internal.suite import (
    MODES,
    assert_lock_order_is_consistent,
    assert_lost_delivery_spends_no_retry_budget,
    assert_lost_notification_reaches_domain_outcome,
    assert_registered_schedule,
    assert_rejection_survives_recovery,
    assert_settlement_wakes_declared_dependents,
    assert_terminal_failure_is_visible_and_rearmable,
    assert_two_workers_one_settlement,
    classified_failure_bindings,
    effect_contract,
    effect_contract_suite,
    eligibility_binding,
    fact_bindings,
    failing_external_call,
    operation_ambiguity,
    persistent_retry_bindings,
    projection_retention,
    required_examples_for,
    retry_bindings,
    snapshot_bindings,
)
from due.django.conformance.bindings import DurableWorkBindings
from due.django.conformance.composition import (
    CancellationExample,
    CompensationExample,
    ProjectionExample,
    SupersessionExample,
)
from due.django.conformance.delivery import WorkTableDelivery
from due.django.conformance.examples import (
    CheckpointedReplay,
    DeliveryExample,
    EffectExamples,
    EligibilityExample,
    FactExamples,
    FailureExample,
    RejectionExample,
    TerminalExample,
)
from due.django.conformance.observations import ObservationExample
from due.django.conformance.scenarios import (
    AdmissionFailureExample,
    ProducerExample,
    SynchronousCallerExample,
    WakeExample,
)

__all__ = [
    "AdmissionFailureExample",
    "CancellationExample",
    "CheckpointedReplay",
    "CompensationExample",
    "DeliveryExample",
    "DurableWorkBindings",
    "EffectExamples",
    "EligibilityExample",
    "FactExamples",
    "FailureExample",
    "MODES",
    "ObservationExample",
    "ProducerExample",
    "ProjectionExample",
    "RejectionExample",
    "SupersessionExample",
    "SynchronousCallerExample",
    "WakeExample",
    "TerminalExample",
    "WorkTableDelivery",
    "assert_lock_order_is_consistent",
    "assert_lost_delivery_spends_no_retry_budget",
    "assert_lost_notification_reaches_domain_outcome",
    "assert_registered_schedule",
    "assert_rejection_survives_recovery",
    "assert_settlement_wakes_declared_dependents",
    "assert_terminal_failure_is_visible_and_rearmable",
    "assert_two_workers_one_settlement",
    "classified_failure_bindings",
    "effect_contract",
    "effect_contract_suite",
    "eligibility_binding",
    "fact_bindings",
    "failing_external_call",
    "operation_ambiguity",
    "projection_retention",
    "required_examples_for",
    "persistent_retry_bindings",
    "retry_bindings",
    "snapshot_bindings",
]
