"""Set-based writes preserve receipts and ordinary recovery after a group rollback."""

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from pytest_obligation.worker_death import WorkerDied

from due.django.conformance.delivery import WorkTableDelivery
from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState
from due.django.testing import make_due
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import SendChunk
from tests.harness.self_work import composed_inline_chunk_environment as composed_inline_chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


def rows_for(capability):
    rows = [capability.model.objects.create(universe="acme", bulk_keys=[str(i)]) for i in range(3)]
    for row in rows:
        make_due(row)
    return rows


@pytest.mark.parametrize("set_based", [False, True])
def test_completion_batches_actual_work_and_receipt_updates(composed_inline_chunk, set_based):
    rows = rows_for(composed_inline_chunk)
    with CaptureQueriesContext(connection) as queries:
        assert all(composed_inline_chunk.attempt_many([row.pk for row in rows], set_based=set_based).values())
    successes = [q["sql"] for q in queries if q["sql"].startswith("UPDATE") and "completed_at" in q["sql"]]
    finishes = [q["sql"] for q in queries if q["sql"].startswith("UPDATE") and '"repair_pending" = false' in q["sql"]]
    assert len(successes) == (1 if set_based else 3), successes
    assert len(finishes) == (1 if set_based else 3), finishes
    for i, row in enumerate(rows):
        row.refresh_from_db()
        assert row.state == DurableWorkState.SUCCEEDED
        assert row.delivered == [str(i)]
        assert SendChunk.accepted_by_work[row.pk] == [str(i)]
        assert not attempts_for(row).get().receipt_pending


@pytest.mark.parametrize("death", [False, True])
def test_failed_set_based_completion_recovers_without_repeating_mail(composed_inline_chunk, monkeypatch, death):
    capability = composed_inline_chunk
    with WorkTableDelivery(capability).session() as delivery:
        rows = rows_for(capability)
        finalize = type(capability).finalize_success

        def fail(self, work, result):
            finalize(self, work, result)
            if work.pk == rows[1].pk:
                raise WorkerDied("after combined work update") if death else ValueError("completion hook failed")

        with monkeypatch.context() as fault:
            fault.setattr(type(capability), "finalize_success", fail)
            with pytest.raises(WorkerDied if death else ExceptionGroup):
                capability.attempt_many([row.pk for row in rows], set_based=True)
        delivery.recover()
        for i, row in enumerate(rows):
            row.refresh_from_db()
            assert row.delivered == [str(i)]
            assert SendChunk.accepted_by_work[row.pk] == [str(i)]
        assert len(SendChunk.calls) == 3


def test_rolled_back_nested_hook_cannot_leak_receipt_clearing_into_the_group(composed_inline_chunk, monkeypatch):
    capability = composed_inline_chunk
    rows = rows_for(capability)
    finalize = type(capability).finalize_success

    def rollback_receipt_marker(self, work, result):
        try:
            with transaction.atomic():
                finalize(self, work, result)
                raise ValueError("the hook's nested transaction rolled back")
        except ValueError:
            pass

    with monkeypatch.context() as fault:
        fault.setattr(type(capability), "finalize_success", rollback_receipt_marker)
        assert all(capability.attempt_many([row.pk for row in rows], set_based=True).values())
    # The success rows committed, but each inner marker write rolled back.
    # A deferred-write collection must not survive that inner rollback.
    assert all(attempts_for(row).get().receipt_pending for row in rows)
    capability.recover()
    assert all(not attempts_for(row).get().receipt_pending for row in rows)
    assert len(SendChunk.calls) == 3
