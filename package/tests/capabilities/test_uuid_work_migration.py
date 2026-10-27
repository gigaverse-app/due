"""Integer receipts survive the additive UUID-key migration."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from due.django.models import EffectAttempt

pytestmark = pytest.mark.django_db(transaction=True)


def test_integer_attempts_survive_uuid_migration_and_integer_only_rollback():
    old_target = [("due", "0003_recoverydeferral_step")]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes("due")
    executor.migrate(old_target)
    try:
        old = executor.loader.project_state(old_target).apps.get_model("due", "EffectAttempt")
        row = old.objects.create(work_model="tests.legacy", work_id=7, claim_token=uuid4(), desired_revision=1)
        MigrationExecutor(connection).migrate(latest)
        upgraded = EffectAttempt.objects.get(pk=row.pk)
        assert upgraded.work_id == 7 and upgraded.work_uuid is None
        assert upgraded.work_identity == 7
        MigrationExecutor(connection).migrate(old_target)
        assert old.objects.get(pk=row.pk).work_id == 7
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.parametrize("both", [False, True])
def test_attempt_has_exactly_one_typed_work_key(both: bool):
    with pytest.raises(IntegrityError), transaction.atomic():
        EffectAttempt.objects.create(
            work_model="tests.invalid",
            work_id=7 if both else None,
            work_uuid=uuid4() if both else None,
            claim_token=uuid4(),
            desired_revision=1,
        )


def test_uuid_attempt_indexes_are_typed_and_sparse():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = %s AND indexname LIKE %s",
            [EffectAttempt._meta.db_table, "effect_attempt_uuid%"],
        )
        definitions = [row[0] for row in cursor.fetchall()]
    assert len(definitions) == 2
    assert all("work_uuid" in definition and "IS NOT NULL" in definition for definition in definitions)
    assert any("repair_pending" in definition for definition in definitions)


def test_uuid_history_lookup_uses_native_index():
    """A large history still finds one UUID occurrence through the typed index."""
    from django.db.models import Q

    target = uuid4()
    EffectAttempt.objects.bulk_create(
        [
            EffectAttempt(
                work_model="tests.uuidplan",
                work_uuid=target if index == 4095 else uuid4(),
                claim_token=uuid4(),
                desired_revision=1,
                repair_pending=index == 4095,
            )
            for index in range(4096)
        ]
    )
    with connection.cursor() as cursor:
        cursor.execute(f'ANALYZE "{EffectAttempt._meta.db_table}"')
    query = EffectAttempt.objects.filter(work_model="tests.uuidplan", work_uuid=target)
    for lookup in (query.order_by("started_at"), query.filter(Q(repair_pending=True))):
        plan = lookup.explain(analyze=True, buffers=True)
        assert "Index" in plan and "effect_attempt_uuid" in plan, plan
        assert "(work_uuid)::text" not in plan, plan
        assert lookup.count() == 1
