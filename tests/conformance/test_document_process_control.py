"""A real process death must not be certified when production recovery is inert."""

import pytest
from pytest_obligation.crash_histories import HistoriesDiverged
from pytest_obligation.process_histories import assert_process_deaths_converge

from examples.documents.effects import PublishDocument
from tests.conformance.document_examples import EXAMPLES


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("document_files")
def test_process_history_rejects_disabled_production_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(PublishDocument, "recover", lambda self: 0)
    with pytest.raises(HistoriesDiverged, match="after admission"):
        assert_process_deaths_converge(EXAMPLES.process_handoffs[0])
