"""The generated proof rejects false seams and unfair callbacks, not just bad metadata."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from due.django.conformance.recovery_isolation import assert_one_poisoned_identity_does_not_stall_recovery
from due.django.conformance.recovery_step_proof import assert_custom_recovery_step
from due.django.conformance.suite import effect_contract_suite
from due.django.conformance.suite_collection import collect_suite
from due.django.runtime.metrics import RecoveryPhase
from tests.harness.enrollment import no_fixture
from tests.harness.recovery_steps import (
    AbandoningCleanup,
    Cleanup,
    DelegatingReap,
    NoopCleanup,
    PagedCleanup,
    SkippingReap,
    UnfairCleanup,
    cleanup_binding,
    collection_only_replay,
    reap_override_binding,
    two_cleanup_bindings,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("seam", [None, "process"], ids=["default-pager-fault", "declared-seam"])
@pytest.mark.parametrize("poison", [False, True])
@pytest.mark.parametrize("implementation", [Cleanup, PagedCleanup])
def test_registered_cleanup_progresses_and_preserves_failed_work(poison, seam, implementation):
    with cleanup_binding(implementation, seam=seam) as (capability, examples):
        assert_custom_recovery_step(capability, examples, RecoveryPhase.DISCOVERY, poison=poison)


@pytest.mark.parametrize(
    "implementation,seam,poison,reason",
    [
        (UnfairCleanup, "process", True, "did not progress"),
        (Cleanup, "unrelated", True, "poison never fired"),
        (NoopCleanup, None, False, "control did not progress"),
        (AbandoningCleanup, None, True, "abandoned"),
    ],
)
def test_false_recovery_claims_fail_at_the_real_runtime_boundary(implementation, seam, poison, reason):
    with cleanup_binding(implementation, seam=seam) as (capability, examples):
        with pytest.raises(AssertionError, match=reason):
            assert_custom_recovery_step(capability, examples, RecoveryPhase.DISCOVERY, poison=poison)


def test_default_fault_rejects_a_loop_that_bypasses_the_shared_pager():
    # A hand-rolled isolate loop never meets the pager's fault: it cannot pass
    # by default, and the failure says how to fix it.
    with cleanup_binding(UnfairCleanup) as (capability, examples):
        with pytest.raises(AssertionError, match="poison never fired") as raised:
            assert_custom_recovery_step(capability, examples, RecoveryPhase.DISCOVERY, poison=True)
    assert any("RecoveryPass.run_page" in note for note in raised.value.__notes__)


@pytest.mark.parametrize("poison", [False, True])
def test_inferred_reap_history_proves_an_override_that_reaches_claim_settlement(poison):
    with reap_override_binding(DelegatingReap) as (capability, examples):
        assert_one_poisoned_identity_does_not_stall_recovery(capability, examples, history="reap", poison=poison)


def test_inferred_reap_history_rejects_an_override_that_skips_claim_settlement():
    # No declaration lends this override a proof: its generated case fails.
    with reap_override_binding(SkippingReap) as (capability, examples):
        with pytest.raises(AssertionError, match="poison never fired"):
            assert_one_poisoned_identity_does_not_stall_recovery(capability, examples, history="reap", poison=True)


def test_collection_requires_the_generated_custom_step_cases():
    with cleanup_binding() as (capability, examples):
        examples = replace(
            examples,
            retry=no_fixture,
            replay=collection_only_replay,
            terminal=no_fixture,
            convergence_not_assessed="Collection-only control; product convergence is checked in its runtime suite.",
        )
        suite = effect_contract_suite(capability, examples=examples)(type("TestCleanup", (), {}))
        # Simulate another collection hook dropping the generated recovery
        # method. Real suite construction and collection must disagree loudly.
        delattr(suite, "test_one_poisoned_identity_does_not_stall_recovery")
        path = Path(__file__).resolve()
        item = SimpleNamespace(cls=suite, path=path, name="test_registered_effect_enrollment")
        config = SimpleNamespace(args=[str(path)])
        with pytest.raises(AssertionError, match=r"lost collected proofs:.*step:discovery-poisoned"):
            collect_suite(cast(pytest.Config, config), [cast(pytest.Function, item)])


@pytest.mark.parametrize("key", ["orphans", "expired"])
@pytest.mark.parametrize("poison", [False, True])
def test_shared_phase_generates_a_real_fault_history_for_each_step(key, poison):
    with two_cleanup_bindings() as (capability, examples):
        assert_custom_recovery_step(capability, examples, key, poison=poison)
