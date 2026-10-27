"""Typed attempt indexes omit the other key family without losing recovery reads."""

from uuid import uuid4

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from due.django.evidence.attempts import attempts_for_model, unknown_effect_attempts
from due.django.models import EffectAttempt
from tests.harness.operation import PublicationWork
from tests.harness.uuid_work import UUIDWork

pytestmark = pytest.mark.django_db(transaction=True)


def integer_indexes():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename=%s AND indexname IN (%s,%s)",
            [EffectAttempt._meta.db_table, "effect_attempt_int_ix", "effect_attempt_int_repair_ix"],
        )
        return [row[0] for row in cursor.fetchall()]


def test_integer_indexes_exclude_uuid_rows():
    definitions = integer_indexes()
    assert len(definitions) == 2
    assert all("work_id IS NOT NULL" in definition for definition in definitions)


@pytest.mark.parametrize(
    "model,key,index",
    [
        (UUIDWork, {"work_uuid": uuid4()}, "effect_attempt_uuid"),
        (PublicationWork, {"work_id": 7}, "effect_attempt_int"),
    ],
)
def test_model_history_and_pending_repair_use_typed_indexes(model, key, index):
    wanted = EffectAttempt.objects.create(
        work_model=model._meta.label_lower,
        **key,
        claim_token=uuid4(),
        desired_revision=1,
    )
    EffectAttempt.objects.bulk_create(
        [
            EffectAttempt(work_model="tests.other", work_id=i + 1, claim_token=uuid4(), desired_revision=1)
            for i in range(4096)
        ]
    )
    with connection.cursor() as cursor:
        cursor.execute(f'ANALYZE "{EffectAttempt._meta.db_table}"')
    for query in (unknown_effect_attempts(model), attempts_for_model(model).filter(repair_pending=True)):
        assert list(query.values_list("pk", flat=True)) == [wanted.pk]
        plan = query.explain(analyze=True, buffers=True)
        assert index in plan, plan


def test_sparse_index_migration_preserves_both_key_families():
    old_target = [("due", "0004_uuid_work_keys")]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes("due")
    executor.migrate(old_target)
    try:
        integer = EffectAttempt.objects.create(
            work_model="tests.integer",
            work_id=7,
            claim_token=uuid4(),
            desired_revision=1,
        )
        uuid = EffectAttempt.objects.create(
            work_model="tests.uuid",
            work_uuid=uuid4(),
            claim_token=uuid4(),
            desired_revision=1,
        )
        MigrationExecutor(connection).migrate(latest)
        assert len(integer_indexes()) == 2
        assert EffectAttempt.objects.get(pk=integer.pk).work_identity == 7
        assert EffectAttempt.objects.get(pk=uuid.pk).work_identity == uuid.work_uuid
        MigrationExecutor(connection).migrate(old_target)
        assert integer_indexes() == []
        assert EffectAttempt.objects.filter(pk__in=[integer.pk, uuid.pk]).count() == 2
    finally:
        MigrationExecutor(connection).migrate(latest)
