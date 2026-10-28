"""Fixed attempt reads retain ORM conversion, routing and transaction boundaries."""

from datetime import datetime
from uuid import UUID, uuid7

import pytest
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from due.django.evidence.attempts import locked_effect_attempt
from due.django.models import EffectAttempt

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("uuid_key", [False, True])
def test_locked_read_preserves_all_current_fields_and_does_not_cache_rows(uuid_key):
    attempt = EffectAttempt.objects.create(
        work_model="tests.work",
        work_id=None if uuid_key else 17,
        work_uuid=uuid7() if uuid_key else None,
        claim_token=uuid7(),
        desired_revision=3,
        receipt={"members": ["a"], "result": [], "diagnostics": {"message": "שלום"}},
    )
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        loaded = locked_effect_attempt(attempt.pk)
    assert len(queries) == 1
    assert not loaded.get_deferred_fields()
    assert isinstance(loaded.pk, UUID) and isinstance(loaded.started_at, datetime)
    for field in EffectAttempt._meta.concrete_fields:
        assert getattr(loaded, field.attname) == getattr(attempt, field.attname)
    EffectAttempt.objects.filter(pk=attempt.pk).update(receipt={"members": ["a"], "result": ["changed"]})
    with transaction.atomic():
        assert locked_effect_attempt(attempt.pk).receipt == {"members": ["a"], "result": ["changed"]}


def test_locked_read_requires_transaction_and_preserves_missing_identity_error():
    with pytest.raises(transaction.TransactionManagementError):
        locked_effect_attempt(uuid7())
    with transaction.atomic(), pytest.raises(EffectAttempt.DoesNotExist):
        locked_effect_attempt(uuid7())


class RejectReplicaRead:
    def db_for_read(self, model, **hints):
        raise AssertionError("locking evidence must use write routing")

    def db_for_write(self, model, **hints):
        return "default"


def test_locked_read_uses_primary_write_routing():
    attempt = EffectAttempt.objects.create(work_model="tests.work", work_id=18, claim_token=uuid7(), desired_revision=1)
    with override_settings(DATABASE_ROUTERS=[RejectReplicaRead()]), transaction.atomic():
        assert locked_effect_attempt(attempt.pk).pk == attempt.pk


def test_locked_read_survives_a_newer_schema_with_an_unmodeled_column():
    """Old workers must select their own fields during a rolling migration."""
    attempt = EffectAttempt.objects.create(work_model="tests.work", work_id=19, claim_token=uuid7(), desired_revision=1)
    table = connection.ops.quote_name(EffectAttempt._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN work_identity bigint")
    try:
        with transaction.atomic():
            assert locked_effect_attempt(attempt.pk).work_identity == 19
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"ALTER TABLE {table} DROP COLUMN work_identity")
