"""A real process death must not be certified when production recovery is inert."""

import pytest
from pytest_obligation.crash_histories import HistoriesDiverged
from pytest_obligation.process_histories import assert_process_deaths_converge

from examples.documents.effects import publish_document
from examples.scheduled.effects import publish_at_deadline
from tests.conformance.document_examples import EXAMPLES as DOCUMENT_EXAMPLES
from tests.conformance.scheduled_examples import EXAMPLES as SCHEDULED_EXAMPLES


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("scenario", ["document", "scheduled"])
def test_process_history_rejects_disabled_production_recovery(scenario: str, monkeypatch: pytest.MonkeyPatch) -> None:
    examples, capability = {
        "document": (DOCUMENT_EXAMPLES, publish_document),
        "scheduled": (SCHEDULED_EXAMPLES, publish_at_deadline),
    }[scenario]
    monkeypatch.setattr(type(capability), "recover", lambda self: 0)
    with examples.environment(), pytest.raises(HistoriesDiverged, match="after admission"):
        assert_process_deaths_converge(examples.process_handoffs[0])
