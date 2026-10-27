"""Read committed membership with one round trip, retaining fail-closed identity checks."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from due.django.evidence.attempts import record_effect_attempt
from due.django.models import EffectAttempt
from due.django.runtime.coordination import claim_work
from tests.harness.bulk import BatchOperation, BatchReceipt, record
from tests.harness.bulk import batch_environment as batch_environment
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = pytest.mark.django_db(transaction=True)


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
