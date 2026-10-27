"""Receipt ingestion reads the canonical batch once under the attempt lock."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from due.django.capabilities.bulk import BulkEvidenceStore
from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState, EffectAttemptOutcome
from due.errors import EffectOutcomeUnknownError
from tests.harness.bulk import BatchOperation, BatchReceipt, record, result
from tests.harness.bulk import batch_environment as batch_environment
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("keys", [("b",), ("b", "c")])
def test_receipt_ingestion_checks_merged_completeness_without_rereading(
    batch_operation: BatchOperation, keys: tuple[str, ...]
) -> None:
    BatchOperation.script.append(result("a"))
    work = record(batch_operation)
    with pytest.raises(EffectOutcomeUnknownError):
        batch_operation.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    with CaptureQueriesContext(connection) as queries:
        batch_operation.record_applied_effect_evidence(attempt, result(*keys))
    receipt_reads = [
        query["sql"]
        for query in queries
        if query["sql"].startswith("SELECT") and f'FROM "{BatchReceipt._meta.db_table}"' in query["sql"]
    ]
    assert len(receipt_reads) == 1
    stored = batch_operation.evidence_store.read(attempt)
    assert {item.key for item in stored.items if item.result is not None} == {"a", *keys}
    attempt.refresh_from_db()
    assert attempt.outcome == (EffectAttemptOutcome.APPLIED if len(keys) == 2 else EffectAttemptOutcome.UNKNOWN)


@pytest.mark.parametrize("hook", ["capability", "store"])
def test_legacy_evidence_writers_still_use_the_read_fallback(batch_operation, monkeypatch, hook):
    owner = BatchOperation if hook == "capability" else BulkEvidenceStore
    name = "record_applied_effect_evidence" if hook == "capability" else "record_applied"
    original = getattr(owner, name)

    def legacy_writer(*args, **kwargs):
        original(*args, **kwargs)  # Older hooks persist correctly but return None.

    monkeypatch.setattr(owner, name, legacy_writer)
    work = record(batch_operation)
    with CaptureQueriesContext(connection) as queries:
        assert batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.published_keys == ["a", "b", "c"]
    assert BatchOperation.calls == [("a", "b", "c")]
    assert BatchOperation.accepted == ["a", "b", "c"]
    # Ingestion, legacy routing fallback, and locked completion.
    assert sum(q["sql"].startswith(f'SELECT "{BatchReceipt._meta.db_table}".') for q in queries) == 3


@pytest.mark.parametrize("late_receipt", ["before_reply", "after_snapshot"])
def test_routing_uses_canonical_evidence_and_settlement_reloads_newer_receipts(
    batch_operation, monkeypatch, late_receipt
):
    send = BatchOperation.send_batch
    accept = BatchOperation.record_applied_effect_evidence

    def partial_reply(self, work, batch, payload):
        send(self, work, batch, payload)  # Provider accepts all three exactly once.
        if late_receipt == "before_reply":
            accept(self, attempts_for(work).get(), result("b", "c"))
        return result("a")

    def accept_with_late_fragment(self, attempt, received):
        snapshot = accept(self, attempt, received)
        if late_receipt == "after_snapshot":
            accept(self, attempt, result("b", "c"))
        return snapshot

    monkeypatch.setattr(BatchOperation, "send_batch", partial_reply)
    monkeypatch.setattr(BatchOperation, "record_applied_effect_evidence", accept_with_late_fragment)
    work = record(batch_operation)
    with CaptureQueriesContext(connection) as queries:
        assert batch_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.published_keys == ["a", "b", "c"]
    assert BatchOperation.calls == [("a", "b", "c")]
    assert BatchOperation.accepted == ["a", "b", "c"]
    assert not attempts_for(work).filter(repair_pending=True).exists()
    assert not BatchReceipt.objects.filter(settlement_pending=True).exists()
    # Claim, attempt, both receipt commits, then completion. A genuinely older
    # partial hint additionally settles progress under its fresh locks first.
    assert sum(q["sql"] == "COMMIT" for q in queries) == (5 if late_receipt == "before_reply" else 6)
