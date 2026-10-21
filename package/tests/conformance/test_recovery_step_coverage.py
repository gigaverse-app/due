"""Every executable recovery callback owes its own proof, regardless of phase name."""

from dataclasses import replace
from functools import wraps
from types import SimpleNamespace
from typing import cast

import pytest
from pytest_obligation.contract import ObligationContractDesignError

from due.django.conformance.examples import EffectExamples
from due.django.conformance.recovery_isolation import recovery_isolation_histories
from due.django.conformance.recovery_steps import RecoveryStepBinding, validate_recovery_step_bindings
from due.django.conformance.suite import effect_contract_suite
from due.django.conformance.suite_collection import collect_suite
from due.django.public import DurableWorkPolicy
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import RecoveryPass, RecoveryStep
from tests.harness.enrollment import TEST_OWNED_CAPABILITIES, no_fixture
from tests.harness.primitive import PrimitiveEffect, PrimitiveOwner, PrimitiveWork


class ExtraRecovery(PrimitiveEffect):
    def derivation_steps(self) -> tuple[RecoveryStep, ...]:
        return (*super().derivation_steps(), RecoveryStep(RecoveryPhase.DISCOVERY, self.clean_orphans))

    def clean_orphans(self, recovery: RecoveryPass) -> int:
        return 0


class OverriddenRecovery(PrimitiveEffect):
    def _reap(self, recovery: RecoveryPass) -> int:
        return 0


class WrongLibraryReceiver(PrimitiveEffect):
    def recorded_work_steps(self) -> tuple[RecoveryStep, ...]:
        other = replace(self)
        return tuple(
            RecoveryStep(step.phase, other._dispatch) if step.phase == RecoveryPhase.DISPATCH else step
            for step in super().recorded_work_steps()
        )


def test_actual_custom_callback_requires_its_own_history():
    capability = ExtraRecovery.for_owner(owner=PrimitiveOwner, work=PrimitiveWork, policy=DurableWorkPolicy())
    assert "step:discovery" in recovery_isolation_histories(capability)


def _custom():
    return ExtraRecovery.for_owner(owner=PrimitiveOwner, work=PrimitiveWork, policy=DurableWorkPolicy())


def test_suite_rejects_custom_step_without_binding_before_running_fixtures():
    with pytest.raises(ObligationContractDesignError, match="missing=.*discovery"):
        effect_contract_suite(_custom(), examples=EffectExamples(make_work=no_fixture, delivery=no_fixture))


@pytest.mark.parametrize(
    "wrong", ["different-callback", "different-instance", "equivalent-instance", "wrapped-name", "stale-phase"]
)
def test_step_declaration_cannot_borrow_another_callback_proof(wrong):
    capability = _custom()
    run, phase = capability.clean_orphans, RecoveryPhase.DISCOVERY
    if wrong == "different-callback":
        run = capability._reap
    elif wrong == "different-instance":
        run = replace(capability, policy=DurableWorkPolicy(batch_size=7)).clean_orphans
    elif wrong == "equivalent-instance":
        run = replace(capability).clean_orphans
    elif wrong == "wrapped-name":

        @wraps(run)
        def wrapper(recovery):
            return 0

        run = wrapper
    else:
        phase = RecoveryPhase.OBSERVATION
    with pytest.raises(ObligationContractDesignError, match="different production callback|stale="):
        validate_recovery_step_bindings(capability, {phase: RecoveryStepBinding(run=run, example=no_fixture)})


def test_actual_custom_binding_is_accepted_without_evaluating_product_fixtures():
    capability = _custom()
    validate_recovery_step_bindings(
        capability, {RecoveryPhase.DISCOVERY: RecoveryStepBinding(run=capability.clean_orphans, example=no_fixture)}
    )


def test_plain_example_cannot_certify_an_arbitrary_callback():
    with pytest.raises(ObligationContractDesignError, match="requires RecoveryStep.paged"):
        validate_recovery_step_bindings(_custom(), {RecoveryPhase.DISCOVERY: no_fixture})


@pytest.mark.parametrize("size", [0, -1])
def test_paged_registration_refuses_a_nonprogressing_page_size(size):
    with pytest.raises(ValueError, match="positive"):
        RecoveryStep.paged(RecoveryPhase.DISCOVERY, no_fixture, no_fixture, size=size)


@pytest.mark.parametrize("capability", TEST_OWNED_CAPABILITIES)
def test_unchanged_library_implementations_retain_their_shared_proofs(capability):
    validate_recovery_step_bindings(capability, {})
    assert not any(history.startswith("step:") for history in recovery_isolation_histories(capability))


def test_an_overridden_builtin_gets_its_shared_history_without_a_declaration():
    # Not a library proof: the override runs the generated reap history itself,
    # which fails when its fault never fires (test_recovery_step_faults.py).
    capability = OverriddenRecovery.for_owner(owner=PrimitiveOwner, work=PrimitiveWork, policy=DurableWorkPolicy())
    validate_recovery_step_bindings(capability, {})
    histories = recovery_isolation_histories(capability)
    assert "reap" in histories
    assert "step:reap" not in histories
    with pytest.raises(ObligationContractDesignError, match="stale=.*reap"):
        validate_recovery_step_bindings(
            capability, {RecoveryPhase.REAP: RecoveryStepBinding(run=capability._reap, example=no_fixture)}
        )


def test_library_implementation_on_another_capability_is_not_an_inherited_proof():
    capability = WrongLibraryReceiver.for_owner(owner=PrimitiveOwner, work=PrimitiveWork, policy=DurableWorkPolicy())
    with pytest.raises(ObligationContractDesignError, match="missing=.*dispatch"):
        validate_recovery_step_bindings(capability, {})


def test_collection_revalidates_changed_callbacks_before_accepting_old_declarations(monkeypatch):
    capability = _custom()
    examples = EffectExamples(
        make_work=no_fixture,
        delivery=no_fixture,
        recovery_steps={RecoveryPhase.DISCOVERY: RecoveryStepBinding(run=capability.clean_orphans, example=no_fixture)},
    )
    validate_recovery_step_bindings(capability, examples.recovery_steps)
    monkeypatch.setattr(ExtraRecovery, "clean_orphans", lambda self, recovery: 0)
    suite = SimpleNamespace(__durable_work_capability__=capability, __durable_work_examples__=examples)
    # Only the collected item envelope is synthetic; exercise the actual
    # collection boundary, before it can register a coverage report.
    with pytest.raises(ObligationContractDesignError, match="different production callback"):
        collect_suite(cast(pytest.Config, None), [cast(pytest.Function, SimpleNamespace(cls=suite))])


@pytest.mark.parametrize("wrong", ["model", "policy", "retention", "evidence"])
def test_factory_cannot_inherit_proof_for_different_bound_inputs(wrong):
    from datetime import timedelta

    from due.django.capabilities._attempt_recovery import attempt_recovery_steps
    from tests.harness.enrollment import EnrollmentOperationWork, Projection

    class FactoryProjection(Projection):
        def derivation_steps(self):
            return attempt_recovery_steps(
                EnrollmentOperationWork if wrong == "model" else self.model,
                replace(self.policy, batch_size=7) if wrong == "policy" else self.policy,
                retention=timedelta(days=1) if wrong == "retention" else self.attempt_retention,
                keep_started_evidence=wrong == "evidence",
                derivation=(),
            )

    capability = FactoryProjection.for_owner(
        owner=PrimitiveOwner, work=PrimitiveWork, policy=DurableWorkPolicy(retention=None)
    )
    with pytest.raises(ObligationContractDesignError, match="missing="):
        validate_recovery_step_bindings(capability, {})


def test_factory_with_current_product_policy_needs_no_extra_declaration():
    from datetime import timedelta

    from due.django.capabilities._attempt_recovery import attempt_recovery_steps
    from tests.harness.enrollment import Projection

    class FactoryProjection(Projection):
        attempt_retention = timedelta(days=14)

        def derivation_steps(self):
            return attempt_recovery_steps(self.model, self.policy, retention=self.attempt_retention, derivation=())

    capability = FactoryProjection.for_owner(
        owner=PrimitiveOwner, work=PrimitiveWork, policy=DurableWorkPolicy(retention=None, batch_size=7)
    )
    validate_recovery_step_bindings(capability, {})


def test_every_named_step_in_a_shared_phase_requires_its_own_example():
    class TwoPages(PrimitiveEffect):
        def derivation_steps(self):
            return tuple(
                RecoveryStep.paged(RecoveryPhase.DISCOVERY, no_fixture, no_fixture, size=2, key=key)
                for key in ("orphans", "expired")
            )

    capability = TwoPages.for_owner(owner=PrimitiveOwner, work=PrimitiveWork, policy=DurableWorkPolicy())
    assert set(recovery_isolation_histories(capability)) >= {"step:orphans", "step:expired"}
    with pytest.raises(ObligationContractDesignError, match="missing=.*expired"):
        validate_recovery_step_bindings(capability, {"orphans": no_fixture})
    validate_recovery_step_bindings(capability, {"orphans": no_fixture, "expired": no_fixture})
    with pytest.raises(ObligationContractDesignError):
        validate_recovery_step_bindings(capability, {RecoveryPhase.DISCOVERY: no_fixture})


@pytest.mark.parametrize("key", ["", "two words", "UpperCase", "x" * 129])
def test_unstable_step_keys_are_rejected(key):
    with pytest.raises(ValueError, match="key"):
        RecoveryStep(RecoveryPhase.DISCOVERY, no_fixture, key=key)
