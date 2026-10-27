"""Optional co-located bulk evidence keeps exact membership and replay permission."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from threading import Event

import pytest
from django.db import IntegrityError, connection, models, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pydantic import JsonValue

from due.bulk import BulkOutcomeState, BulkResult, BulkRetryPolicy
from due.django.conformance.worker_connection import database_worker_connection
from due.django.evidence.attempts import attempts_for, prune_resolved_attempts
from due.django.models import DurableWorkState, EffectAttempt, EffectAttemptOutcome
from due.errors import EffectOutcomeUnknownError
from tests.harness.bulk import BatchReceipt, result
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import Chunk, InlineChunk, InlineSendChunk, SendChunk
from tests.harness.self_work import inline_chunk_delivery as inline_delivery
from tests.harness.self_work import inline_chunk_environment as inline_chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("retry", [False, True])
def test_inline_evidence_delivers_without_a_companion_receipt(inline_chunk, retry):
    capability = replace(inline_delivery, bulk_policy=BulkRetryPolicy(3, timedelta(seconds=30)))
    work = InlineChunk.objects.create(universe="acme", bulk_keys=["a", "b"])
    if retry:
        SendChunk.script.append(
            BulkResult(items=(*result("a").items, *result("b", state=BulkOutcomeState.RETRYABLE).items))
        )
    with CaptureQueriesContext(connection) as queries:
        assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.delivered == ["a", "b"]
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]
    assert [keys for _, keys in SendChunk.calls] == ([("a", "b"), ("b",)] if retry else [("a", "b")])
    assert not BatchReceipt.objects.exists()
    assert not [q for q in queries if BatchReceipt._meta.db_table in q["sql"]]
    attempts = list(attempts_for(work).order_by("started_at"))
    assert all(attempt.outcome == EffectAttemptOutcome.APPLIED for attempt in attempts)
    assert all(not attempt.repair_pending and not attempt.receipt_pending for attempt in attempts)
    members = []
    for attempt in attempts:
        assert attempt.receipt is not None
        members.append(attempt.receipt["members"])
    assert members == ([["a", "b"], ["b"]] if retry else [["a", "b"]])


def test_inline_late_receipts_settle_without_repeating_the_provider(inline_chunk):
    work = InlineChunk.objects.create(universe="acme", bulk_keys=["a", "b"])
    SendChunk.script.append(result("a"))
    with pytest.raises(EffectOutcomeUnknownError):
        inline_delivery.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    assert attempt.outcome == EffectAttemptOutcome.UNKNOWN
    assert attempt.receipt is not None
    assert attempt.receipt["members"] == ["a", "b"]
    inline_delivery.record_applied_effect_evidence(attempt, result("b"))
    inline_delivery.derive()
    work.refresh_from_db()
    attempt.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.delivered == ["a", "b"]
    assert len(SendChunk.calls) == 1
    assert not attempt.receipt_pending and not attempt.repair_pending
    assert not BatchReceipt.objects.exists()


def test_inline_receipt_conflict_preserves_committed_evidence(inline_chunk):
    work = InlineChunk.objects.create(universe="acme", bulk_keys=["a", "b"])
    SendChunk.script.append(result("a"))
    with pytest.raises(EffectOutcomeUnknownError):
        inline_delivery.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    original = attempt.receipt
    with pytest.raises(ValueError):
        inline_delivery.record_applied_effect_evidence(attempt, result("a", state=BulkOutcomeState.RETRYABLE))
    attempt.refresh_from_db()
    assert attempt.receipt == original
    assert attempt.outcome == EffectAttemptOutcome.UNKNOWN
    assert len(SendChunk.calls) == 1


def test_inline_diagnostics_survive_semantic_acceptance_and_cannot_cross_capabilities(inline_chunk, monkeypatch):
    diagnostics = {"message_id": "provider-123", "status": "accepted"}
    send = InlineSendChunk.send_batch

    def provider_reply(self, work, batch, payload):
        result = send(self, work, batch, payload)
        self.record_receipt_diagnostics(batch.attempt_id, diagnostics)
        return result

    monkeypatch.setattr(InlineSendChunk, "send_batch", provider_reply)
    work = InlineChunk.objects.create(universe="acme", bulk_keys=["a"])
    assert inline_delivery.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    assert attempt.receipt is not None
    assert attempt.receipt["diagnostics"] == diagnostics
    assert attempt.receipt["members"] == ["a"]
    assert attempt.outcome == EffectAttemptOutcome.APPLIED
    assert not attempt.repair_pending and not attempt.receipt_pending
    # A capability-scoped method cannot annotate another work model's attempt.
    other = replace(inline_delivery, model=Chunk, owner_model=Chunk)
    with pytest.raises(EffectAttempt.DoesNotExist):
        other.record_receipt_diagnostics(attempt.pk, {"wrong": True})
    attempt.refresh_from_db()
    assert attempt.receipt is not None and attempt.receipt["diagnostics"] == diagnostics


@pytest.mark.parametrize(
    "first_writer,waiting_writer", [("verdict", "diagnostics"), ("diagnostics", "verdict"), ("verdict", "verdict")]
)
def test_inline_waiting_writer_preserves_committed_receipt(inline_chunk, first_writer, waiting_writer):
    work = InlineChunk.objects.create(universe="acme", bulk_keys=["a", "b", "c"])
    SendChunk.script.append(result("a"))
    with pytest.raises(EffectOutcomeUnknownError):
        inline_delivery.attempt_execution(work.pk)
    stale_attempt = attempts_for(work).get()
    waiting = Event()
    diagnostics: JsonValue = {"message_id": "provider-456"}

    def observe_wait(execute, sql, params, many, context):
        if EffectAttempt._meta.db_table in sql and ("FOR UPDATE" in sql or sql.startswith("UPDATE")):
            waiting.set()
        return execute(sql, params, many, context)

    def write_after_lock():
        with database_worker_connection(statement_timeout_ms=5000), connection.execute_wrapper(observe_wait):
            if waiting_writer == "diagnostics":
                inline_delivery.record_receipt_diagnostics(stale_attempt.pk, diagnostics)
            else:
                inline_delivery.record_applied_effect_evidence(stale_attempt, result("c"))

    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = None

        def after_lock(execute, sql, params, many, context):
            nonlocal writer
            value = execute(sql, params, many, context)
            if (
                writer is None
                and EffectAttempt._meta.db_table in sql
                and ("FOR UPDATE" in sql or sql.startswith("UPDATE"))
            ):
                writer = pool.submit(write_after_lock)
                assert waiting.wait(5), "competing writer never reached the attempt"
            return value

        with (
            transaction.atomic() if first_writer == "diagnostics" else nullcontext(),
            connection.execute_wrapper(after_lock),
        ):
            if first_writer == "verdict":
                inline_delivery.record_applied_effect_evidence(stale_attempt, result("b"))
            else:
                inline_delivery.record_receipt_diagnostics(stale_attempt.pk, diagnostics)
        assert writer is not None
        writer.result(timeout=10)
    observed = attempts_for(work).get()
    assert observed.receipt is not None
    if "diagnostics" in (first_writer, waiting_writer):
        assert observed.receipt["diagnostics"] == diagnostics
    expected = (
        {"a"} | ({"b"} if first_writer == "verdict" else set()) | ({"c"} if waiting_writer == "verdict" else set())
    )
    assert {
        item.key for item in inline_delivery.evidence_store.read(observed).items if item.result is not None
    } == expected
    assert observed.outcome == (EffectAttemptOutcome.APPLIED if len(expected) == 3 else EffectAttemptOutcome.UNKNOWN)
    assert len(SendChunk.calls) == 1


def test_inline_receipt_is_preserved_by_attempt_retention(inline_chunk):
    work = InlineChunk.objects.create(universe="acme", bulk_keys=["a"])
    assert inline_delivery.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    assert prune_resolved_attempts(InlineChunk, before=timezone.now() + timedelta(days=1), batch_size=10) == 0
    attempt.refresh_from_db()
    assert attempt.receipt is not None and attempt.receipt["members"] == ["a"]


def test_inline_acceptance_failure_preserves_prior_evidence_and_replay_block(inline_chunk):
    work = InlineChunk.objects.create(universe="acme", bulk_keys=["a", "b"])
    SendChunk.script.append(result("a"))
    with pytest.raises(EffectOutcomeUnknownError):
        inline_delivery.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    original = attempt.receipt
    constraint = models.CheckConstraint(name="test_refuse_acceptance", condition=~models.Q(outcome="applied"))
    with connection.schema_editor() as editor:
        editor.add_constraint(EffectAttempt, constraint)
    try:
        with pytest.raises(IntegrityError, match="test_refuse_acceptance"):
            inline_delivery.record_applied_effect_evidence(attempt, result("b"))
        attempt.refresh_from_db()
        assert attempt.receipt == original
        assert attempt.outcome == EffectAttemptOutcome.UNKNOWN and attempt.repair_pending
        assert not inline_delivery.attempt_execution(work.pk)
        assert len(SendChunk.calls) == 1
    finally:
        with connection.schema_editor() as editor:
            editor.remove_constraint(EffectAttempt, constraint)
    inline_delivery.record_applied_effect_evidence(attempt, result("b"))
    inline_delivery.derive()
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.delivered == ["a", "b"]
    assert len(SendChunk.calls) == 1


def test_inline_completion_failure_keeps_receipt_owed_until_recovery(inline_chunk, monkeypatch):
    work = InlineChunk.objects.create(universe="acme", bulk_keys=["a", "b"])

    def failed_hook(self, work, attempt):
        raise RuntimeError("completion unavailable")

    with monkeypatch.context() as fault:
        fault.setattr(InlineSendChunk, "finalize_success", failed_hook)
        with pytest.raises(RuntimeError, match="completion unavailable"):
            inline_delivery.attempt_execution(work.pk)
        for _ in range(3):
            work.refresh_from_db()
            attempt = attempts_for(work).get()
            assert work.delivered == [] and work.bulk_progress == []
            assert attempt.repair_pending and attempt.receipt_pending
            assert inline_delivery.evidence_store.read(attempt) == result("a", "b")
            assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]
            assert SendChunk.calls == [(work.pk, ("a", "b"))]
            with pytest.raises(RuntimeError, match="completion unavailable"):
                inline_delivery.settle_from_applied_effect_evidence(work.pk)
    inline_delivery.recover()
    work.refresh_from_db()
    attempt.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED and work.delivered == ["a", "b"]
    assert not attempt.repair_pending and not attempt.receipt_pending
    assert SendChunk.calls == [(work.pk, ("a", "b"))]
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]
