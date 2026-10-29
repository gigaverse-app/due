"""Independent provider acceptances survive bounded, shared settlement commits."""

from datetime import datetime
from uuid import uuid7

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from django.db import connection
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext
from pytest_obligation.worker_death import WorkerDied

from due.django.conformance.delivery import WorkTableDelivery
from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState
from due.django.testing import make_due
from due.errors import EffectNotAppliedError
from tests.harness.bulk import result
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import SendChunk
from tests.harness.self_work import composed_inline_chunk_environment as composed_inline_chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


def chunks(capability, count=3):
    rows = [capability.model.objects.create(universe="acme", bulk_keys=[str(i)]) for i in range(count)]
    for row in rows:
        make_due(row)
    return rows


def test_bounded_settlement_shares_commit_and_preserves_provider_boundary(composed_inline_chunk, monkeypatch):
    capability = composed_inline_chunk
    rows = chunks(capability)
    send = SendChunk.send_batch

    def observe(self, work, batch, payload):
        assert not connection.in_atomic_block
        if work.pk == rows[1].pk:
            rows[0].refresh_from_db()
            assert rows[0].state == DurableWorkState.CLAIMED
            assert rows[0].delivered == []
            assert attempts_for(rows[0]).get().outcome == "applied"
        if work.pk == rows[2].pk:
            rows[0].refresh_from_db()
            assert rows[0].delivered == ["0"]
        return send(self, work, batch, payload)

    monkeypatch.setattr(SendChunk, "send_batch", observe)
    with CaptureQueriesContext(connection) as queries:
        outcomes = capability.attempt_many((row.pk for row in rows), settlement_batch_size=2)
    assert outcomes == dict.fromkeys((row.pk for row in rows), True)
    for i, row in enumerate(rows):
        row.refresh_from_db()
        assert row.delivered == [str(i)]
        assert SendChunk.accepted_by_work[row.pk] == [str(i)]
    # Three independent claims, entries and receipts, followed by two settlements.
    assert sum(query["sql"] == "COMMIT" for query in queries) == 11


def test_failed_hook_does_not_block_healthy_rows_or_repeat_provider(composed_inline_chunk, monkeypatch):
    capability = composed_inline_chunk
    rows = chunks(capability)

    def broken(self, work, result):
        if work.pk == rows[1].pk:
            raise ValueError("broken product hook")

    with monkeypatch.context() as fault:
        fault.setattr(SendChunk, "finalize_batch", broken)
        with pytest.raises(ExceptionGroup, match="settlement"):
            capability.attempt_many([row.pk for row in rows], settlement_batch_size=3)
    assert list(capability.model.objects.order_by("id").values_list("delivered", flat=True)) == [["0"], [], ["2"]]
    capability.derive()
    for i, row in enumerate(rows):
        row.refresh_from_db()
        assert row.delivered == [str(i)]
        assert SendChunk.accepted_by_work[row.pk] == [str(i)]
    assert len(SendChunk.calls) == 3


def test_original_single_execution_commits_before_return(composed_inline_chunk):
    capability = composed_inline_chunk
    row = chunks(capability, 1)[0]
    assert capability.attempt_execution(row.pk)
    row.refresh_from_db()
    assert row.delivered == ["0"]


def test_death_during_group_rolls_back_all_product_writes_and_recovers(composed_inline_chunk, monkeypatch):
    capability = composed_inline_chunk
    with WorkTableDelivery(capability).session() as delivery:
        rows = chunks(capability)

        def die(self, work, result):
            if work.pk == rows[1].pk:
                raise WorkerDied("process died halfway through local settlement")

        with monkeypatch.context() as fault:
            fault.setattr(SendChunk, "finalize_batch", die)
            with pytest.raises(WorkerDied):
                capability.attempt_many([row.pk for row in rows], settlement_batch_size=3)
        assert list(capability.model.objects.values_list("delivered", flat=True)) == [[], [], []]
        assert all(attempts_for(row).get().outcome == "applied" for row in rows)
        delivery.recover()
        for i, row in enumerate(rows):
            row.refresh_from_db()
            assert row.delivered == [str(i)]
            assert SendChunk.accepted_by_work[row.pk] == [str(i)]


@pytest.mark.parametrize("changed", ["token", "revision", "state"])
def test_replaced_fence_cannot_publish_buffered_completion(composed_inline_chunk, changed):
    capability = composed_inline_chunk
    rows = chunks(capability, 2)

    def identities():
        yield rows[0].pk
        # FAULT: a competing owner replaces this captured claim before flush.
        changes = {
            "token": {"fencing_token": uuid7()},
            "revision": {"desired_revision": rows[0].desired_revision + 1},
            "state": {"state": DurableWorkState.UNKNOWN, "lease_expires_at": None},
        }
        capability.model.objects.filter(pk=rows[0].pk).update(**changes[changed])
        yield rows[1].pk

    outcomes = capability.attempt_many(identities(), settlement_batch_size=2)
    rows[0].refresh_from_db()
    rows[1].refresh_from_db()
    assert outcomes == {rows[0].pk: False, rows[1].pk: True}
    assert rows[0].delivered == [] and rows[1].delivered == ["1"]
    assert attempts_for(rows[0]).get().repair_pending


def test_provider_failure_flushes_prior_receipt_without_entering_later_rows(composed_inline_chunk):
    capability = composed_inline_chunk
    rows = chunks(capability)
    SendChunk.script.extend([result("0"), EffectNotAppliedError("provider refused")])
    with pytest.raises(EffectNotAppliedError):
        capability.attempt_many([row.pk for row in rows])
    rows[0].refresh_from_db()
    assert rows[0].delivered == ["0"]
    assert [pk for pk, _ in SendChunk.calls] == [rows[0].pk, rows[1].pk]
    assert not attempts_for(rows[2]).exists()


@pytest.mark.parametrize("size", [0, -1, 65, True, 1.5])
def test_invalid_bound_rejected_before_provider_calls(composed_inline_chunk, size):
    row = chunks(composed_inline_chunk, 1)[0]
    with pytest.raises((ValueError, TypeError), match="settlement_batch_size"):
        composed_inline_chunk.attempt_many([row.pk], settlement_batch_size=size)
    assert SendChunk.calls == []


def test_soft_time_limit_does_not_start_per_row_settlement_fallback(composed_inline_chunk, monkeypatch):
    capability = composed_inline_chunk
    rows = chunks(capability)
    reached = []

    def stop(self, work, result):
        reached.append(work.pk)
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(SendChunk, "finalize_batch", stop)
    with pytest.raises(SoftTimeLimitExceeded):
        capability.attempt_many([row.pk for row in rows], settlement_batch_size=3)
    assert reached == [rows[0].pk]
    assert list(capability.model.objects.values_list("delivered", flat=True)) == [[], [], []]


def test_grouped_completion_preserves_model_saves_and_signals(composed_inline_chunk, monkeypatch):
    capability = composed_inline_chunk
    rows = chunks(capability)
    saves = []
    signals = []
    save = capability.model.save

    def record_save(instance, *args, **kwargs):
        if instance.state == DurableWorkState.SUCCEEDED:
            saves.append(instance.pk)
        return save(instance, *args, **kwargs)

    def observe(sender, instance, **kwargs):
        if instance.state == DurableWorkState.SUCCEEDED:
            assert isinstance(instance.completed_at, datetime)
            assert connection.in_atomic_block
            signals.append(instance.pk)

    monkeypatch.setattr(capability.model, "save", record_save)
    post_save.connect(observe, sender=capability.model)
    try:
        capability.attempt_many([row.pk for row in rows], settlement_batch_size=3)
    finally:
        post_save.disconnect(observe, sender=capability.model)
    assert saves == signals == [row.pk for row in rows]
