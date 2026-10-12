"""Application proofs reject missing or consistently wrong product outcomes."""

from dataclasses import replace

import pytest
from pytest_obligation.process_histories import ProcessHistory, assert_process_deaths_converge

from due.django.conformance.suite import EffectExamples
from examples.books.models import IndexBookWork
from examples.scheduled.models import PublicationDeadline
from tests.conformance.book_examples import INDEX_EXAMPLES
from tests.conformance.scheduled_examples import EXAMPLES

type ApplicationExamples = EffectExamples[IndexBookWork] | EffectExamples[PublicationDeadline]


@pytest.mark.parametrize("examples", [INDEX_EXAMPLES, EXAMPLES], ids=["books", "scheduled"])
def test_process_binding_requires_an_independent_expected_outcome(examples: ApplicationExamples) -> None:
    history = examples.process_handoffs[0]
    weaker = ProcessHistory(**history.model_dump(exclude={"expected"}))
    with pytest.raises(ValueError, match="declare expected independently"):
        replace(examples, process_handoffs=(weaker,))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("examples", [INDEX_EXAMPLES, EXAMPLES], ids=["books", "scheduled"])
def test_consistently_wrong_provider_result_cannot_pass_application_proof(
    examples: ApplicationExamples, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DUE_TEST_CORRUPT_PROVIDER", "1")
    with examples.environment(), pytest.raises(AssertionError, match="declared product outcome"):
        assert_process_deaths_converge(examples.process_handoffs[0])
