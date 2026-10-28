"""Sensitivity checks reuse real SQL operations and the existing product/provider oracle."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import patch

import pytest
from pytest_obligation.helpers import BindingScopeError

from due.django.conformance.regressions import EffectRegression, assert_effect_contract_detects_regression
from due.django.conformance.scenarios import assert_uncertainty_blocks_resend
from due.django.conformance.suite import DeliveryExample, EffectExamples
from due.django.testing import WorkerDied, make_due
from tests.harness.operation import Publication, PublicationWork, delivery_example, record
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import SendChunk
from tests.harness.self_work import chunk_environment as chunk_environment
from tests.harness.self_work_contract import self_work_examples

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("control", [EffectRegression.UNCERTAINTY_SETTLEMENT, EffectRegression.DUPLICATE_EFFECT])
def test_operation_contract_detects_regression_and_restores_the_provider(
    publication: Publication, control: EffectRegression
) -> None:
    original_effect = Publication.perform_effect
    original_settlement = Publication._finalize_unknown
    other = replace(publication)
    scopes: list[str] = []

    @contextmanager
    def example() -> Iterator[DeliveryExample[PublicationWork, str]]:
        scopes.append("enter")
        try:
            if control == EffectRegression.DUPLICATE_EFFECT:
                before = len(Publication.calls)
                assert other.attempt_execution(record(other).pk)
                assert len(Publication.calls) == before + 1, "the control changed another capability instance"
            yield delivery_example(record(publication), expected="applied")
        finally:
            scopes.append("exit")

    @contextmanager
    def uncertain() -> Iterator[DeliveryExample[PublicationWork, str]]:
        with patch.object(
            Publication, "record_applied_effect_evidence", side_effect=WorkerDied("accepted; receipt lost")
        ):
            assert_uncertainty_blocks_resend(other, delivery_example(record(other), expected="unknown"))
            with example() as delivery:
                yield replace(delivery, expected="unknown")

    examples = EffectExamples(
        make_work=lambda: record(publication),
        delivery=example,
        receipt_repair=example,
        uncertainties={"lost-receipt": uncertain},
    )
    assert_effect_contract_detects_regression(publication, examples=examples, regression=control)
    assert scopes == ["enter", "exit"] * 3
    assert Publication.perform_effect is original_effect
    assert Publication._finalize_unknown is original_settlement


def test_missing_provider_observation_is_a_baseline_failure(publication: Publication) -> None:
    original = Publication.perform_effect

    def blind() -> DeliveryExample[PublicationWork, str]:
        return replace(delivery_example(record(publication), expected="applied"), effect_calls=None)

    with pytest.raises(AssertionError, match="baseline failed.*independent provider-call observation"):
        assert_effect_contract_detects_regression(
            publication,
            examples=EffectExamples(make_work=lambda: record(publication), delivery=blind, receipt_repair=blind),
            regression=EffectRegression.DUPLICATE_EFFECT,
        )
    assert Publication.perform_effect is original


def test_control_restores_its_patch_when_example_cleanup_fails(publication: Publication) -> None:
    original = Publication.perform_effect
    runs = 0

    @contextmanager
    def example() -> Iterator[DeliveryExample[PublicationWork, str]]:
        nonlocal runs
        runs += 1
        try:
            yield delivery_example(record(publication), expected="applied")
        finally:
            if runs == 2:
                raise RuntimeError("provider fixture cleanup failed")

    with pytest.raises(RuntimeError, match="provider fixture cleanup failed"):
        assert_effect_contract_detects_regression(
            publication,
            examples=EffectExamples(make_work=lambda: record(publication), delivery=example, receipt_repair=example),
            regression=EffectRegression.DUPLICATE_EFFECT,
        )
    assert Publication.perform_effect is original


def test_matching_cleanup_assertion_is_not_regression_evidence(publication: Publication) -> None:
    original = Publication.perform_effect
    runs = 0

    @contextmanager
    def example() -> Iterator[DeliveryExample[PublicationWork, str]]:
        nonlocal runs
        runs += 1
        try:
            yield delivery_example(record(publication), expected="applied")
        finally:
            if runs == 2:
                raise AssertionError("receipt history skipped or repeated provider I/O: cleanup failed")

    with pytest.raises(BindingScopeError, match="cleanup failed"):
        assert_effect_contract_detects_regression(
            publication,
            examples=EffectExamples(make_work=lambda: record(publication), delivery=example, receipt_repair=example),
            regression=EffectRegression.DUPLICATE_EFFECT,
        )
    assert Publication.perform_effect is original


def test_uncertainty_selection_requires_an_existing_scenario(publication: Publication) -> None:
    examples = EffectExamples(
        make_work=lambda: record(publication), delivery=lambda: delivery_example(record(publication), "applied")
    )
    with pytest.raises(ValueError, match="requires an uncertainty scenario"):
        assert_effect_contract_detects_regression(
            publication, examples=examples, regression=EffectRegression.UNCERTAINTY_SETTLEMENT
        )


def test_bulk_progress_control_rejects_scalar_operations(publication: Publication) -> None:
    examples = EffectExamples(
        make_work=lambda: record(publication), delivery=lambda: delivery_example(record(publication), "applied")
    )
    with pytest.raises(ValueError, match="requires a bulk external operation"):
        assert_effect_contract_detects_regression(
            publication, examples=examples, regression=EffectRegression.BULK_PROGRESS
        )


def test_bulk_progress_control_is_scoped_and_restores_the_write(self_chunk: SendChunk) -> None:
    original = type(self_chunk)._record_progress
    other = replace(self_chunk)
    examples = self_work_examples(self_chunk, "self_chunk")
    delivery = examples.receipt_repair
    assert delivery is not None
    runs = 0

    def scoped():
        nonlocal runs
        runs += 1
        work = other.model.objects.create(universe="acme", bulk_keys=["other"])
        make_due(work)
        assert other.attempt_execution(work.pk)
        work.refresh_from_db()
        assert work.delivered == ["other"]
        return delivery()

    assert_effect_contract_detects_regression(
        self_chunk, examples=replace(examples, receipt_repair=scoped), regression=EffectRegression.BULK_PROGRESS
    )
    assert runs == 3
    assert type(self_chunk)._record_progress is original
