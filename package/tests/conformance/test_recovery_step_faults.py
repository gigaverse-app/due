"""The generated proof rejects false seams and unfair callbacks, not just bad metadata."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from due.django.conformance.recovery_step_proof import assert_custom_recovery_step
from due.django.conformance.suite import effect_contract_suite
from due.django.conformance.suite_collection import collect_suite
from due.django.runtime.metrics import RecoveryPhase
from tests.harness.enrollment import no_fixture
from tests.harness.recovery_steps import (
    AbandoningCleanup,
    Cleanup,
    NoopCleanup,
    UnfairCleanup,
    cleanup_binding,
    collection_only_replay,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("poison", [False, True])
def test_registered_cleanup_progresses_and_preserves_failed_work(poison):
    with cleanup_binding() as (capability, examples):
        assert_custom_recovery_step(capability, examples, RecoveryPhase.DISCOVERY, poison=poison)


@pytest.mark.parametrize(
    "implementation,wrong_seam,poison,reason",
    [
        (UnfairCleanup, False, True, "did not progress"),
        (Cleanup, True, True, "poison never fired"),
        (NoopCleanup, False, False, "control did not progress"),
        (AbandoningCleanup, False, True, "abandoned"),
    ],
)
def test_false_recovery_claims_fail_at_the_real_runtime_boundary(implementation, wrong_seam, poison, reason):
    with cleanup_binding(implementation, wrong_seam=wrong_seam) as (capability, examples):
        with pytest.raises(AssertionError, match=reason):
            assert_custom_recovery_step(capability, examples, RecoveryPhase.DISCOVERY, poison=poison)


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
