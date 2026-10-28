"""Application fields share the fenced progress write and its rollback boundary."""

import pytest
from django.db import IntegrityError, connection

from due.bulk import BulkResult
from due.django.models import DurableWorkState
from tests.harness.bulk import BatchOperation, BatchWork, ItemResult, record
from tests.harness.bulk import batch_environment as batch_environment
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "field_name",
    [
        "id",
        "state",
        "fencing_token",
        "lease_expires_at",
        "bulk_progress",
        "bulk_keys",
        "universe",
        "owner",
        "owner_id",
        "missing",
    ],
)
@pytest.mark.parametrize("combined", [False, True])
def test_progress_projection_cannot_write_identity_inputs_or_coordination(
    batch_operation: BatchOperation, field_name: str, combined: bool, monkeypatch
) -> None:
    monkeypatch.setattr(BatchOperation, "combine_completed_progress", combined)

    class InvalidProjection(BatchOperation):
        def progress_updates(self, work: BatchWork, progress: BulkResult[ItemResult]) -> dict[str, object]:
            return {field_name: None}

    invalid = InvalidProjection.for_owner(
        owner=PrimitiveOwner,
        work=BatchWork,
        input_fields=("universe", "bulk_keys"),
        policy=batch_operation.policy,
    )
    work = record(invalid)
    owner_id = work.owner_id
    with pytest.raises(ValueError, match="progress_updates"):
        invalid.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.UNKNOWN
    assert work.bulk_progress == [] and work.published_keys == []
    assert work.bulk_keys == ["a", "b", "c"] and work.universe == "acme"
    assert work.owner_id == owner_id
    batch_operation.recover()
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.published_keys == ["a", "b", "c"]
    assert BatchOperation.calls == [("a", "b", "c")]


@pytest.mark.parametrize("combined", [False, True])
def test_a_product_constraint_failure_rolls_back_progress_and_recovery_reapplies_the_receipt(
    batch_operation: BatchOperation,
    combined: bool,
    monkeypatch,
) -> None:
    monkeypatch.setattr(BatchOperation, "combine_completed_progress", combined)
    work = record(batch_operation)
    table = connection.ops.quote_name(BatchWork._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT test_progress_limit CHECK (jsonb_array_length(published_keys) < 3)"
        )
    try:
        with pytest.raises(IntegrityError):
            batch_operation.attempt_execution(work.pk)
        work.refresh_from_db()
        assert work.bulk_progress == [] and work.published_keys == []
        assert work.state == DurableWorkState.UNKNOWN
        assert BatchOperation.accepted_by_work[work.pk] == ["a", "b", "c"]
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"ALTER TABLE {table} DROP CONSTRAINT test_progress_limit")
    batch_operation.recover()
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.published_keys == ["a", "b", "c"]
    assert BatchOperation.calls == [("a", "b", "c")]
