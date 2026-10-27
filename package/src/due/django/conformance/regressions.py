"""Opt-in sensitivity controls for the existing generated work-table proofs.

These deliberately break one selected capability in this test process. They
never change an example's observer or expectations. Run them with isolated test
resources, never alongside application workers in the same process.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from functools import partial
from typing import cast
from unittest.mock import patch

from django.db.models import Model
from pytest_obligation.regression_controls import assert_detects_regression

from due.django.capabilities.operations import DurableExternalOperation, ExternalOperationWork
from due.django.conformance.binding import entered_binding
from due.django.conformance.examples import EffectExamples
from due.django.conformance.scenarios import assert_receipt_repairs_product, assert_uncertainty_blocks_resend
from due.django.conformance.suite import assert_lost_notification_reaches_domain_outcome
from due.django.public import DurableWorkCapability, UniverseDurableWork
from due.django.testing import hold_nudges
from due.observations import AppliedEffectEvidence


class EffectRegression(StrEnum):
    """The precise broken behavior a control proves observable, not a coverage grade."""

    UNCERTAINTY_SETTLEMENT = "omit-uncertainty-settlement"
    LOST_PUBLICATION = "disable-lost-publication-recovery"
    DUPLICATE_EFFECT = "repeat-external-operation"


def assert_effect_contract_detects_regression[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT],
    *,
    examples: EffectExamples[W],
    regression: EffectRegression,
    uncertainty: str | None = None,
) -> None:
    """Run a generated proof clean, broken, and restored with fresh example scopes.

    Lost publication uses ``delivery`` and the registered production sweep.
    The other controls require an evidence-backed external operation:
    ``uncertainties`` observes the product's uncertainty settlement, while
    ``receipt_repair`` counts real external entries. Select ``uncertainty`` when
    more than one named scenario exists. No extra assertions are invented here.

    This does not prove all uncertainty policies or physical exactly-once
    delivery: duplicate detection proves the existing oracle sees repeated
    external entries. A blind/broken baseline, unrelated error or cleanup
    failure propagates instead of counting as successful detection.
    """
    if not isinstance(regression, EffectRegression):
        raise ValueError("regression must be an EffectRegression")
    if uncertainty is not None and regression != EffectRegression.UNCERTAINTY_SETTLEMENT:
        raise ValueError("uncertainty selects only the uncertainty-settlement control")
    if regression == EffectRegression.LOST_PUBLICATION:
        check = partial(assert_lost_notification_reaches_domain_outcome, capability, examples)
        mutation = partial(_without_recovery, capability)
        match = "configured recovery did not publish the producer's exact work"
    else:
        if not isinstance(capability, DurableExternalOperation):
            raise ValueError("uncertainty and duplicate-entry controls require an external operation")
        # isinstance narrows the capability but cannot refine its associated W type parameter.
        operation = cast(DurableExternalOperation[Model, ExternalOperationWork, ResultT], capability)
        if regression == EffectRegression.UNCERTAINTY_SETTLEMENT:
            choices = examples.uncertainties
            if not choices:
                raise ValueError("uncertainty-settlement control requires an uncertainty scenario")
            if uncertainty is None:
                if len(choices) != 1:
                    raise ValueError("select one named uncertainty scenario")
                uncertainty = next(iter(choices))
            if uncertainty not in choices:
                raise ValueError(f"unknown uncertainty scenario: {uncertainty}")
            factory = choices[uncertainty]
            mutation = partial(_without_uncertainty_settlement, operation)
            match = "uncertainty preserves the required product outcome"
        else:
            if examples.receipt_repair is None:
                raise ValueError("duplicate-entry control requires receipt_repair")
            factory = examples.receipt_repair
            mutation = partial(_with_duplicate_effect, operation)
            match = "receipt history skipped or repeated provider I/O"

        def check() -> None:
            with entered_binding(examples.environment), hold_nudges(), entered_binding(factory) as example:
                if regression == EffectRegression.UNCERTAINTY_SETTLEMENT:
                    assert_uncertainty_blocks_resend(capability, example)
                else:
                    assert_receipt_repairs_product(capability, example, interruption="live")

    # One owner validates the baseline, exact failure and restored baseline.
    assert_detects_regression(check, regression=mutation, match=match)


@contextmanager
def _without_recovery[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT],
) -> Iterator[None]:
    original = type(capability).recover
    reached = False

    def omitted(instance: DurableWorkCapability[W, ResultT]) -> None:
        nonlocal reached
        if instance is capability:
            reached = True
        else:
            original(instance)

    with patch.object(type(capability), "recover", omitted):
        yield
    assert reached, "regression control never reached the selected recovery"


@contextmanager
def _without_uncertainty_settlement[OwnerT: Model, W: ExternalOperationWork, ResultT](
    capability: DurableExternalOperation[OwnerT, W, ResultT],
) -> Iterator[None]:
    original = type(capability)._finalize_unknown
    reached = False

    def omitted(instance: DurableExternalOperation[OwnerT, W, ResultT], work: W) -> None:
        nonlocal reached
        if instance is capability:
            reached = True
        else:
            original(instance, work)

    with patch.object(type(capability), "_finalize_unknown", omitted):
        yield
    assert reached, "regression control never reached the selected uncertainty settlement"


@contextmanager
def _with_duplicate_effect[OwnerT: Model, W: ExternalOperationWork, ResultT](
    capability: DurableExternalOperation[OwnerT, W, ResultT],
) -> Iterator[None]:
    original = type(capability).perform_effect
    reached = False

    def repeated(
        instance: DurableExternalOperation[OwnerT, W, ResultT], work: W
    ) -> ResultT | AppliedEffectEvidence[ResultT]:
        nonlocal reached
        if instance is capability:
            reached = True
            original(instance, work)
        return original(instance, work)

    with patch.object(type(capability), "perform_effect", repeated):
        yield
    assert reached, "regression control never reached the selected external operation"
