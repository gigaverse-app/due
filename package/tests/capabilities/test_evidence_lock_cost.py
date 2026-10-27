"""Evidence acceptance reuses its identity lock without weakening atomicity."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from due.django.evidence.attempts import record_effect_attempt
from due.django.models import EffectAttempt, EffectAttemptOutcome
from due.django.runtime.coordination import claim_work
from due.errors import EffectRejection
from tests.harness.operation import ProviderResult, Publication, Receipt, record
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("writer", ["receipt", "rejection"])
def test_acceptance_reads_the_attempt_once(publication: Publication, writer: str) -> None:
    work = record(publication)
    claim = claim_work(type(work), work.pk, lease_duration=publication.policy.lease_duration)
    assert claim is not None
    attempt = record_effect_attempt(claim)
    with CaptureQueriesContext(connection) as queries:
        if writer == "receipt":
            publication.record_applied_effect_evidence(attempt, ProviderResult(value="applied"))
        else:
            publication.record_rejection(attempt, EffectRejection(message="rejected"))
    sql = [query["sql"] for query in queries]
    attempt_reads = [query for query in sql if query.startswith("SELECT") and 'FROM "due_effectattempt"' in query]
    assert len(attempt_reads) == 1, attempt_reads
    assert "FOR UPDATE" in attempt_reads[0]
    attempt_writes = [query for query in sql if query.startswith('UPDATE "due_effectattempt"')]
    assert len(attempt_writes) == 1, attempt_writes
    # JSON receipt creation keeps its own insert-conflict savepoint. Outcome
    # acceptance must not add another savepoint while holding the same lock.
    assert sum(query.startswith("SAVEPOINT") for query in sql) == (1 if writer == "receipt" else 0), sql
    persisted = EffectAttempt.objects.get(pk=attempt.pk)
    if writer == "receipt":
        assert persisted.outcome == EffectAttemptOutcome.APPLIED
        assert persisted.repair_pending
        assert Receipt.objects.filter(attempt=persisted).exists()
    else:
        assert persisted.outcome == EffectAttemptOutcome.NOT_APPLIED
        assert not persisted.repair_pending
        assert persisted.rejection == EffectRejection(message="rejected").model_dump(mode="json")
