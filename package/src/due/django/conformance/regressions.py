"""Opt-in sensitivity controls for the existing generated work-table proofs.

These deliberately break one selected capability in this test process. They
never change an example's observer or expectations. Run them with isolated test
resources, never alongside application workers in the same process.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import cast
from unittest.mock import patch

from django.db.models import Model
from pydantic import BaseModel
from pytest_obligation.regression_controls import assert_detects_regression

from due.bulk import BulkOutcomeState, BulkResult, batch_state
from due.django.capabilities.bulk import BulkExternalOperationWork, DurableBulkExternalOperation
from due.django.capabilities.operations import DurableExternalOperation, ExternalOperationWork
from due.django.conformance.binding import entered_binding
from due.django.conformance.examples import EffectExamples
from due.django.conformance.examples import EffectRegression as EffectRegression
from due.django.conformance.scenarios import assert_receipt_repairs_product, assert_uncertainty_blocks_resend
from due.django.conformance.suite import assert_lost_notification_reaches_domain_outcome
from due.django.models import EffectAttempt
from due.django.public import DurableWorkCapability, UniverseDurableWork
from due.django.runtime.coordination import WorkClaim
from due.django.testing import hold_nudges
from due.observations import AppliedEffectEvidence


@dataclass(frozen=True)
class RegressionCase:
    regression: EffectRegression
    uncertainty: str | None = None

    @property
    def id(self) -> str:
        return self.regression.value + (f"-{self.uncertainty}" if self.uncertainty is not None else "")


def regression_cases[W: UniverseDurableWork](examples: EffectExamples[W]) -> tuple[RegressionCase, ...]:
    """One case owner for suite generation and required collection/report evidence."""
    if any(not isinstance(control, EffectRegression) for control in examples.regressions):
        raise ValueError("regressions must contain EffectRegression declarations")
    if len(set(examples.regressions)) != len(examples.regressions):
        raise ValueError("regressions must not repeat a control")
    cases = []
    for control in examples.regressions:
        if control == EffectRegression.UNCERTAINTY_SETTLEMENT:
            if not examples.uncertainties:
                raise ValueError("uncertainty-settlement control requires an uncertainty scenario")
            cases.extend(RegressionCase(control, name) for name in examples.uncertainties)
        else:
            cases.append(RegressionCase(control))
    return tuple(cases)


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
    ``receipt_repair`` observes persisted product progress and real external entries.
    Select ``uncertainty`` when
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
        elif regression == EffectRegression.BULK_PROGRESS:
            if not isinstance(capability, DurableBulkExternalOperation):
                raise ValueError("bulk-progress control requires a bulk external operation")
            if examples.receipt_repair is None:
                raise ValueError("bulk-progress control requires receipt_repair")
            factory = examples.receipt_repair
            mutation = partial(_without_bulk_progress, capability)
            match = "receipt recovery reaches the required product outcome"
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
def _without_bulk_progress[OwnerT: Model, W: BulkExternalOperationWork, ResultT: BaseModel](
    capability: DurableBulkExternalOperation[OwnerT, W, ResultT],
) -> Iterator[None]:
    original = type(capability)._record_progress
    reached = False

    def omitted(
        instance: DurableBulkExternalOperation[OwnerT, W, ResultT],
        work: W,
        result: BulkResult[ResultT],
        *,
        input_fields: tuple[str, ...] | None,
    ) -> BulkOutcomeState:
        nonlocal reached
        if instance is not capability:
            return original(instance, work, result, input_fields=input_fields)
        reached = True
        current, _fields = instance._prepare_progress(work, result, input_fields=input_fields)
        return batch_state(work.bulk_keys, current)

    with patch.object(type(capability), "_record_progress", omitted):
        yield
    assert reached, "regression control never reached the selected bulk progress write"


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
    # Repeat the live call returned by the committed crossing. Bulk execution
    # binds exact membership here; patching its loading entry point would no
    # longer exercise the fault. Scalar operations use this same boundary.
    original = type(capability)._record_crossing
    reached = False

    def repeated(
        instance: DurableExternalOperation[OwnerT, W, ResultT], claim: WorkClaim[W]
    ) -> tuple[EffectAttempt, Callable[[W], ResultT | AppliedEffectEvidence[ResultT]]]:
        attempt, perform = original(instance, claim)
        if instance is not capability:
            return attempt, perform

        def repeat(work: W) -> ResultT | AppliedEffectEvidence[ResultT]:
            nonlocal reached
            reached = True
            perform(work)
            return perform(work)

        return attempt, repeat

    with patch.object(type(capability), "_record_crossing", repeated):
        yield
    assert reached, "regression control never reached the selected external operation"
