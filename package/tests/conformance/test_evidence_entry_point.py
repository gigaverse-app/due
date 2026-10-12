"""
Self-test crash-ambiguity proofs that settle through the application's receipt entry point.

With ``evidence_entry`` bound, profile C's late-evidence invariants deliver the
receipt through that entry point and settle through registered recovery,
instead of the library's evidence recorder. An entry point that drops the
receipt, or writes the product without recording evidence, leaves the
ambiguity unresolved and fails the same shared assertion a correct one passes.
"""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_obligation.contract import ObligationContractDesignError
from pytest_obligation.profiles.crash_ambiguity import assert_ambiguity_contract

from due.django.conformance.bindings import DurableWorkBindings
from due.django.conformance.examples import EffectExamples
from due.django.conformance.suite import effect_contract, operation_ambiguity
from tests.harness.operation import ProviderResult, Publication, PublicationWork, record
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import PrimitiveOwner, PrimitiveWork, primitive
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)
from tests.harness.primitive import (
    root_registration_fixture as root_registration_fixture,
)

type EntryPoint = Callable[[PublicationWork, ProviderResult], object]


def receipt(work: PublicationWork) -> ProviderResult:
    # ARRANGE: the provider's canonical receipt for the operation it applied.
    return ProviderResult(value=work.payload)


def correlating_webhook(publication: Publication) -> EntryPoint:
    # REAL PRODUCTION: a callback that correlates the receipt with its occurrence and records it.
    return lambda work, result: publication.record_observed_effect_evidence(work.pk, result)


def uncorrelated_webhook(publication: Publication) -> EntryPoint:
    # Defect: the callback cannot find the occurrence the receipt names, so it drops it.
    return lambda work, result: publication.record_observed_effect_evidence(work.pk + 1_000_000, result)


def product_only_webhook(publication: Publication) -> EntryPoint:
    # Defect: the callback publishes the product but never records the evidence that settles the work.
    return lambda work, result: PrimitiveOwner.objects.filter(pk=work.owner_id).update(value=result.value)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("entry", "defect"),
    [
        (correlating_webhook, None),
        (uncorrelated_webhook, "not terminal"),
        (product_only_webhook, "not terminal"),
    ],
    ids=["correlates-and-records", "drops-the-receipt", "bypasses-evidence"],
)
def test_late_evidence_settles_only_through_a_working_entry_point(
    publication: Publication, entry: Callable[[Publication], EntryPoint], defect: str | None
) -> None:
    bindings = DurableWorkBindings(publication, lambda: record(publication))
    with operation_ambiguity(bindings, receipt, entry(publication)) as ambiguity:
        if defect is None:
            assert_ambiguity_contract(ambiguity)
        else:
            with pytest.raises(AssertionError, match=defect):
                assert_ambiguity_contract(ambiguity)


def declared_only(*args: object, **kwargs: object) -> Any:
    # ARRANGE: never entered; only the contract declaration is under test.
    raise AssertionError("a declaration-only binding must not execute")


@pytest.mark.no_db
def test_evidence_entry_requires_an_operation() -> None:
    examples: EffectExamples[PrimitiveWork] = EffectExamples(
        make_work=declared_only,
        delivery=declared_only,
        retry=declared_only,
        replay=declared_only,
        terminal=declared_only,
        immutable_inputs="An immutable domain occurrence.",
        perturb_inputs=declared_only,
        producers={"declared": declared_only},
        evidence_entry=declared_only,
    )
    with pytest.raises(ObligationContractDesignError, match="evidence_entry requires the operation execution mode"):
        effect_contract(primitive, examples=examples)
