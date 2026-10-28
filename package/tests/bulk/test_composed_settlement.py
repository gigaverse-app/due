"""Completed progress can share the existing completion write when explicitly declared."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState
from due.django.testing import make_due
from due.errors import EffectOutcomeUnknownError
from tests.harness.bulk import result
from tests.harness.bulk_settlement import assert_settlement_boundaries
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import SendChunk
from tests.harness.self_work import chunk_environment as chunk_environment
from tests.harness.self_work import inline_chunk_environment as inline_chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("fixture", ["self_chunk", "inline_chunk"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("recovery", [False, True])
def test_completed_progress_shares_only_the_opted_in_completion_write(
    request, monkeypatch, fixture: str, enabled: bool, recovery: bool
) -> None:
    capability: SendChunk = request.getfixturevalue(fixture)
    monkeypatch.setattr(type(capability), "combine_completed_progress", enabled, raising=False)
    work = capability.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    make_due(work)
    if recovery:
        monkeypatch.setattr(SendChunk, "lose_reply", True)
        with pytest.raises(EffectOutcomeUnknownError):
            capability.attempt_execution(work.pk)
        monkeypatch.setattr(SendChunk, "lose_reply", False)
        capability.record_applied_effect_evidence(attempts_for(work).get(), result("a", "b"))
    with CaptureQueriesContext(connection) as queries:
        if recovery:
            capability.derive()
        else:
            assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.delivered == ["a", "b"] and work.product_status == "a,b"
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]
    assert len(SendChunk.calls) == 1
    progress_writes = [q["sql"] for q in queries if q["sql"].startswith("UPDATE") and '"bulk_progress" =' in q["sql"]]
    assert len(progress_writes) == 1
    assert ('"state" =' in progress_writes[0]) is enabled


@pytest.mark.parametrize("fixture", ["self_chunk", "inline_chunk"])
@pytest.mark.parametrize("phase", ["batch", "success"])
def test_composed_hook_failure_keeps_receipt_and_recovers_without_resend(request, monkeypatch, fixture, phase):
    capability = request.getfixturevalue(fixture)
    monkeypatch.setattr(type(capability), "combine_completed_progress", True)
    assert_settlement_boundaries(capability, fail_at=phase, combined=True)
