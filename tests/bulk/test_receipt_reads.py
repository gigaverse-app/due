"""Receipt ingestion reads the canonical batch once under the attempt lock."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from due.django.evidence.attempts import attempts_for
from due.django.models import EffectAttemptOutcome
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
