"""Pending inline evidence is selected independently of work state and retained history."""

from uuid import uuid4

import pytest
from django.db import connection, transaction

from due.django.capabilities.bulk import BulkEvidenceStore
from due.django.models import EffectAttempt
from tests.harness.bulk import ItemResult

pytestmark = pytest.mark.django_db(transaction=True)


def test_pending_inline_receipts_use_sparse_index_and_missing_index_control():
    model = "tests.inline_selection"
    retained = {"members": ["a"], "result": []}
    EffectAttempt.objects.bulk_create(
        [
            EffectAttempt(
                work_model=model,
                work_id=i + 1,
                claim_token=uuid4(),
                desired_revision=1,
                receipt=retained,
                receipt_pending=i == 4095,
                repair_pending=False,
            )
            for i in range(4096)
        ]
    )
    store = BulkEvidenceStore(None, ItemResult)
    query = store.pending_attempts(model).order_by("pk")[:10]
    wanted = EffectAttempt.objects.get(receipt_pending=True)
    assert list(query.values_list("pk", flat=True)) == [wanted.pk]
    with connection.cursor() as cursor:
        cursor.execute(f'ANALYZE "{EffectAttempt._meta.db_table}"')
    plan = query.explain(analyze=True, buffers=True)
    assert "effect_attempt_receipt_ix" in plan, plan
    # Prove the sparse selection actually depends on this index. Roll back
    # the deliberate schema damage so later tests use the deployed schema.
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("DROP INDEX effect_attempt_receipt_ix")
        damaged = query.explain(analyze=True, buffers=True)
        assert "effect_attempt_receipt_ix" not in damaged, damaged
        assert "Rows Removed by Filter: 4095" in damaged, damaged
        transaction.set_rollback(True)
