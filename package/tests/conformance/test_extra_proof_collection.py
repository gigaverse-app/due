"""Shared extra proofs stay part of Due's required generated inventory."""

from dataclasses import replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pytest_obligation.contract import ExtraProof
from pytest_obligation.integrations.postgres_plans import assert_plan_is_index_served
from pytest_obligation.references.lifecycle import admission_example, settlement_example

from due.django.conformance.suite import effect_contract, effect_contract_suite
from due.django.conformance.suite_collection import collect_suite
from tests.harness.enrollment import no_fixture
from tests.harness.recovery_steps import cleanup_binding, collection_only_replay


@pytest.mark.django_db(transaction=True)
def test_extra_proof_is_forwarded_and_cannot_disappear_from_collection() -> None:
    # Collection-only control: the SQL probe's execution and fault controls live
    # in pytest-obligation; this checks Due's forwarding and required inventory.
    extra = ExtraProof(
        name="dependency_index",
        run=partial(assert_plan_is_index_served, name="dependency", plan={}, table="work"),
        transactional=True,
    )
    with cleanup_binding() as (capability, examples):
        examples = replace(
            examples,
            retry=no_fixture,
            replay=collection_only_replay,
            terminal=no_fixture,
            convergence_not_assessed="Collection-only control; runtime convergence has its own suite.",
            extras=(extra,),
        )
        assert effect_contract(capability, examples=examples).extras == (extra,)
        suite = effect_contract_suite(capability, examples=examples)(type("TestExtra", (), {}))
        path = Path(__file__).resolve()
        item = SimpleNamespace(cls=suite, path=path, name="test_registered_effect_enrollment")
        config = SimpleNamespace(args=[str(path)])
        with pytest.raises(AssertionError, match="lost collected proofs:.*dependency_index"):
            collect_suite(cast(pytest.Config, config), [cast(pytest.Function, item)])


@pytest.mark.django_db(transaction=True)
def test_lifecycle_histories_are_forwarded_and_required_in_collection() -> None:
    # Only generation is under test here; obligation owns oracle sensitivity.
    with cleanup_binding() as (capability, examples):
        examples = replace(
            examples,
            retry=no_fixture,
            replay=collection_only_replay,
            terminal=no_fixture,
            convergence_not_assessed="Collection-only control; runtime convergence has its own suite.",
            admission_overlaps={"repair_then_full": admission_example},
            settlement_interleavings={"provider_read": settlement_example},
        )
        contract = effect_contract(capability, examples=examples)
        assert contract.admission_overlaps == examples.admission_overlaps
        assert contract.settlement_interleavings == examples.settlement_interleavings
        suite = effect_contract_suite(capability, examples=examples)(type("TestLifecycle", (), {}))
        path = Path(__file__).resolve()
        item = SimpleNamespace(cls=suite, path=path, name="test_registered_effect_enrollment")
        config = SimpleNamespace(args=[str(path)])
        with pytest.raises(AssertionError, match="lost collected proofs:.*admission-overlap"):
            collect_suite(cast(pytest.Config, config), [cast(pytest.Function, item)])
