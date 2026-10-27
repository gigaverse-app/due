"""A UUID business row can own its durable occurrence without a companion row."""

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from due.django.evidence.attempts import attempts_for
from due.django.public import DurableWorkState
from due.errors import EffectOutcomeUnknownError
from tests.harness.bulk import result
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import Chunk, SendChunk
from tests.harness.self_work import chunk_environment as chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


def test_business_insert_is_the_only_admission_row(self_chunk: SendChunk) -> None:
    chunk = Chunk.objects.create(universe="acme", bulk_keys=["a", "b"])
    with CaptureQueriesContext(connection) as queries:
        with self_chunk.atomic(chunk.pk, notify_worker=False) as pending:
            assert pending.locked_owner is pending.existing_work
    assert len([q for q in queries if "FOR UPDATE" in q["sql"]]) == 1
    assert self_chunk.attempt_execution(chunk.pk)
    chunk.refresh_from_db()
    assert chunk.state == DurableWorkState.SUCCEEDED
    assert chunk.delivered == ["a", "b"]
    assert attempts_for(chunk).get().work_uuid == chunk.pk
    assert SendChunk.calls == [(chunk.pk, ("a", "b"))]
    assert not self_chunk.attempt_execution(chunk.pk)
    assert len(SendChunk.calls) == 1


def test_unknown_uuid_chunk_settles_late_receipts_without_resending(self_chunk: SendChunk) -> None:
    chunk = Chunk.objects.create(universe="acme", bulk_keys=["a", "b"])
    SendChunk.lose_reply = True
    with pytest.raises(EffectOutcomeUnknownError):
        self_chunk.attempt_execution(chunk.pk)
    attempt = attempts_for(chunk).get()
    self_chunk.record_applied_effect_evidence(attempt, result("a", "b"))
    self_chunk.derive()
    chunk.refresh_from_db()
    assert chunk.state == DurableWorkState.SUCCEEDED
    assert chunk.delivered == ["a", "b"]
    assert SendChunk.calls == [(chunk.pk, ("a", "b"))]


def test_rollback_removes_business_intent_and_work_together(self_chunk: SendChunk) -> None:
    with pytest.raises(RuntimeError), transaction.atomic():
        chunk = Chunk.objects.create(universe="acme", bulk_keys=["a"])
        raise RuntimeError("business transaction rolled back")
    assert not Chunk.objects.filter(pk=chunk.pk).exists()
    assert not self_chunk.attempt_execution(chunk.pk)
    assert SendChunk.calls == []


def test_another_engines_rows_are_never_selected_or_executed(self_chunk: SendChunk) -> None:
    chunk = Chunk.objects.create(state="legacy", universe="acme", bulk_keys=["a"])
    assert not self_chunk.due_work().filter(pk=chunk.pk).exists()
    assert not self_chunk.attempt_execution(chunk.pk)
    self_chunk.recover()
    chunk.refresh_from_db()
    assert chunk.state == "legacy"
    assert SendChunk.calls == []
    assert not attempts_for(chunk).exists()


@pytest.mark.parametrize(
    "states", [{"ready": "another engine owns this state"}, {"not_a_choice": "another engine owns this state"}]
)
def test_other_lifecycle_declarations_cannot_hide_due_or_invent_states(monkeypatch, states) -> None:
    monkeypatch.setattr(Chunk, "work_external_states", states)
    with pytest.raises(ValueError, match="external lifecycle states"):
        SendChunk.for_work(work=Chunk, input_fields=("universe", "bulk_keys"))
