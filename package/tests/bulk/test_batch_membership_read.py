"""Read committed membership with one round trip, retaining fail-closed identity checks."""

import pytest
from django.db import DatabaseError, connection, transaction
from django.test.utils import CaptureQueriesContext

from due.django.evidence.attempts import record_effect_attempt
from due.django.models import EffectAttempt
from due.django.runtime.coordination import WorkExecutionContext, claim_work
from due.errors import EffectOutcomeUnknownError
from tests.harness.bulk import BatchOperation, BatchReceipt, record
from tests.harness.bulk import batch_environment as batch_environment
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import SendChunk
from tests.harness.self_work import chunk_environment as chunk_environment
from tests.harness.self_work import inline_chunk_environment as inline_chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("layout", ["self_chunk", "inline_chunk"])
def test_live_crossing_reuses_membership_without_a_select(request, layout):
    operation = request.getfixturevalue(layout)
    work = operation.model.objects.create(universe="acme", bulk_keys=["a", "b", "c"])
    with CaptureQueriesContext(connection) as queries:
        assert operation.attempt_execution(work.pk)
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b", "c"]
    assert not [q["sql"] for q in queries if 'AS "captured_members"' in q["sql"]]


@pytest.mark.parametrize("layout", ["self_chunk", "inline_chunk"])
def test_live_membership_comes_from_the_locked_row_not_the_input_snapshot(request, layout):
    operation = request.getfixturevalue(layout)
    work = operation.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    claim = claim_work(type(work), work.pk, lease_duration=operation.policy.lease_duration)
    assert claim is not None
    claim.work.bulk_keys = ["stale-worker-input"]
    attempt, _ = operation._apply_with_evidence(
        claim.work, WorkExecutionContext(claim, operation.policy.lease_duration)
    )
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]
    assert operation.evidence_store.members(attempt) == ("a", "b")


@pytest.mark.parametrize("layout", ["self_chunk", "inline_chunk"])
def test_failed_started_hook_never_exposes_captured_members_to_the_provider(request, layout, monkeypatch):
    operation = request.getfixturevalue(layout)
    work = operation.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    reached = []

    def fail(self, current):
        # The attempt and membership exist inside this transaction, but are not committed.
        reached.append(self.current_batch(current).keys)
        raise RuntimeError("started hook failed")

    monkeypatch.setattr(SendChunk, "finalize_started", fail)
    with pytest.raises(RuntimeError, match="started hook failed"):
        operation.attempt_execution(work.pk)
    assert reached == [("a", "b")]
    assert SendChunk.calls == []
    assert not EffectAttempt.objects.exists()
    assert not BatchReceipt.objects.exists()


@pytest.mark.parametrize("layout", ["self_chunk", "inline_chunk"])
def test_failed_commit_discards_the_prepared_provider_call(request, layout, monkeypatch):
    operation = request.getfixturevalue(layout)
    work = operation.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    claim = claim_work(type(work), work.pk, lease_duration=operation.policy.lease_duration)
    assert claim is not None
    reached = []

    def fail_commit(self):
        reached.append(operation.current_batch(claim.work).keys)
        raise DatabaseError("attempt commit failed")

    with monkeypatch.context() as patch:
        # Patching the ConnectionProxy instance restores a bound method onto the
        # underlying connection, shadowing later class-level crash instrumentation.
        patch.setattr(type(transaction.get_connection()), "commit", fail_commit)
        with pytest.raises(DatabaseError, match="attempt commit failed"):
            operation._apply_with_evidence(claim.work, WorkExecutionContext(claim, operation.policy.lease_duration))
    assert reached == [("a", "b")]
    assert SendChunk.calls == []
    assert not EffectAttempt.objects.exists()
    assert not BatchReceipt.objects.exists()


@pytest.mark.parametrize("layout", ["self_chunk", "inline_chunk"])
def test_existing_unknown_attempt_still_blocks_a_new_crossing(request, layout):
    operation = request.getfixturevalue(layout)
    work = operation.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    claim = claim_work(type(work), work.pk, lease_duration=operation.policy.lease_duration)
    assert claim is not None
    operation._record_attempt(claim)
    with pytest.raises(EffectOutcomeUnknownError):
        operation._apply_with_evidence(claim.work, WorkExecutionContext(claim, operation.policy.lease_duration))
    assert SendChunk.calls == []
    assert EffectAttempt.objects.count() == 1


@pytest.mark.parametrize("layout", ["self_chunk", "inline_chunk"])
def test_nested_crossings_keep_their_own_membership(request, layout, monkeypatch):
    operation = request.getfixturevalue(layout)
    outer = operation.model.objects.create(universe="acme", bulk_keys=["outer"])
    inner = operation.model.objects.create(universe="acme", bulk_keys=["inner"])

    def prepare(self, work, batch):
        if work.pk == outer.pk:
            assert self.attempt_execution(inner.pk)

    monkeypatch.setattr(SendChunk, "prepare_batch", prepare)
    assert operation.attempt_execution(outer.pk)
    assert SendChunk.accepted_by_work == {inner.pk: ["inner"], outer.pk: ["outer"]}


def entered_batch(operation):
    work = record(operation)
    claim = claim_work(type(work), work.pk, lease_duration=operation.policy.lease_duration)
    assert claim is not None
    attempt = record_effect_attempt(claim, before_commit=operation._before_attempt_commits)
    return claim.work, attempt


def test_captured_membership_reads_once_and_ignores_a_mutated_work_snapshot(batch_operation: BatchOperation):
    work, attempt = entered_batch(batch_operation)
    work.bulk_keys = ["not-the-committed-members"]
    with CaptureQueriesContext(connection) as queries:
        batch = batch_operation.current_batch(work)
    assert batch.attempt_id == attempt.pk and batch.keys == ("a", "b", "c")
    assert len(queries) == 1, [query["sql"] for query in queries]


def test_missing_receipt_still_refuses_the_crossing(batch_operation: BatchOperation):
    work, attempt = entered_batch(batch_operation)
    BatchReceipt.objects.filter(attempt=attempt).delete()
    with pytest.raises(BatchReceipt.DoesNotExist):
        batch_operation.current_batch(work)


def test_a_second_unknown_attempt_cannot_be_hidden_by_its_missing_receipt(batch_operation: BatchOperation):
    work, attempt = entered_batch(batch_operation)
    EffectAttempt.objects.create(
        work_model=attempt.work_model,
        work_id=attempt.work_id,
        claim_token=attempt.claim_token,
        desired_revision=attempt.desired_revision,
    )
    with pytest.raises(EffectAttempt.MultipleObjectsReturned):
        batch_operation.current_batch(work)
