"""Optional diagnostics share the canonical receipt's write and failure boundary."""

import pytest
from django.db import IntegrityError, connection, models
from django.test.utils import CaptureQueriesContext

from due.bulk import BulkResult
from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState, EffectAttempt
from due.errors import EffectOutcomeUnknownError
from tests.harness.bulk import result
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import SendChunk
from tests.harness.self_work import composed_inline_chunk_environment as composed_inline_chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


def test_diagnostics_commit_with_the_canonical_result_in_one_update(composed_inline_chunk):
    operation = composed_inline_chunk
    diagnostics = {"request_id": "provider-request", "responses": [{"id": "a"}, {"id": "b"}]}
    SendChunk.script.append(BulkResult(items=result("a", "b").items, diagnostics=diagnostics))
    work = operation.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    with CaptureQueriesContext(connection) as queries:
        assert operation.attempt_execution(work.pk)
    work.refresh_from_db()
    attempt = attempts_for(work).get()
    assert work.state == DurableWorkState.SUCCEEDED and work.delivered == ["a", "b"]
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]
    assert attempt.receipt is not None
    assert attempt.receipt["diagnostics"] == diagnostics
    writes = [q["sql"] for q in queries if q["sql"].startswith(f'UPDATE "{EffectAttempt._meta.db_table}"')]
    # Canonical receipt/outcome, then settlement markers. No diagnostics-only write.
    assert len(writes) == 2
    assert '"diagnostics"' in writes[0] and '"result"' in writes[0]


def test_partial_diagnostics_never_authorize_replay_and_late_results_preserve_them(composed_inline_chunk):
    operation = composed_inline_chunk
    SendChunk.script.append(BulkResult(items=result("a").items, diagnostics={"request_id": "partial"}))
    work = operation.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    with pytest.raises(EffectOutcomeUnknownError):
        operation.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    assert attempt.receipt is not None
    assert attempt.receipt["diagnostics"] == {"request_id": "partial"}
    assert not operation.attempt_execution(work.pk)
    operation.record_applied_effect_evidence(attempt, result("b"))
    operation.recover()
    attempt.refresh_from_db()
    work.refresh_from_db()
    assert attempt.receipt["diagnostics"] == {"request_id": "partial"}
    assert work.delivered == ["a", "b"]
    assert SendChunk.calls == [(work.pk, ("a", "b"))]


def test_failed_receipt_write_does_not_commit_diagnostics_separately(composed_inline_chunk):
    operation = composed_inline_chunk
    SendChunk.script.append(result("a"))
    work = operation.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    with pytest.raises(EffectOutcomeUnknownError):
        operation.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    before = attempt.receipt
    constraint = models.CheckConstraint(name="test_combined_receipt_refused", condition=~models.Q(outcome="applied"))
    with connection.schema_editor() as editor:
        editor.add_constraint(EffectAttempt, constraint)
    try:
        with pytest.raises(IntegrityError, match="test_combined_receipt_refused"):
            operation.record_applied_effect_evidence(
                attempt, BulkResult(items=result("b").items, diagnostics={"new": True})
            )
        attempt.refresh_from_db()
        assert attempt.receipt == before
        assert attempt.repair_pending and not operation.attempt_execution(work.pk)
    finally:
        with connection.schema_editor() as editor:
            editor.remove_constraint(EffectAttempt, constraint)
    operation.record_applied_effect_evidence(attempt, BulkResult(items=result("b").items, diagnostics={"new": True}))
    operation.recover()
    work.refresh_from_db()
    attempt.refresh_from_db()
    assert work.delivered == ["a", "b"]
    assert attempt.receipt is not None
    assert attempt.receipt["diagnostics"] == {"new": True}
    assert SendChunk.calls == [(work.pk, ("a", "b"))]
