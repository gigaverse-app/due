"""New attempt IDs are ordered; existing receipt identity survives rollout/rollback."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from due.django.evidence.attempts import attempts_for
from due.django.models import EffectAttempt
from tests.harness.operation import ProviderResult, Receipt, record
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = pytest.mark.django_db(transaction=True)


def test_new_attempts_use_ordered_uuid7_and_replay_keeps_the_receipt(publication):
    ids = []
    for _ in range(3):
        work = record(publication)
        assert publication.attempt_execution(work.pk)
        assert not publication.attempt_execution(work.pk)
        attempt = attempts_for(work).get()
        assert attempt.pk.version == 7
        assert attempt.claim_token.version == 4
        assert publication.evidence_store.read(attempt) == ProviderResult(value="applied")
        ids.append(attempt.pk)
    assert list(EffectAttempt.objects.filter(pk__in=ids).order_by("pk").values_list("pk", flat=True)) == ids


def test_ordered_id_migration_keeps_legacy_receipts_and_reverses(publication):
    before = [("due", "0004_uuid_work_keys")]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes("due")
    executor.migrate(before)
    try:
        old = executor.loader.project_state(before).apps.get_model("due", "EffectAttempt")
        legacy = old.objects.create(
            work_model="tests.legacy", work_id=1, claim_token=old._meta.get_field("id").default(), desired_revision=1
        )
        assert legacy.pk.version == 4
        Receipt.objects.create(attempt_id=legacy.pk, result={"value": "legacy"})
        MigrationExecutor(connection).migrate(latest)
        work = record(publication)
        publication.attempt_execution(work.pk)
        current = attempts_for(work).get()
        assert current.pk.version == 7
        assert Receipt.objects.get(pk=legacy.pk).result == {"value": "legacy"}
        MigrationExecutor(connection).migrate(before)
        assert EffectAttempt.objects.get(pk=current.pk).pk.version == 7
        assert Receipt.objects.get(pk=legacy.pk).result == {"value": "legacy"}
    finally:
        MigrationExecutor(connection).migrate(latest)
