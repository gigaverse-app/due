"""Receipt storage is additive; rollback cannot erase possible provider effects."""

from uuid import uuid4

import pytest
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor

from due.django.models import EffectAttempt

pytestmark = pytest.mark.django_db(transaction=True)


def test_inline_migration_preserves_old_attempts_and_refuses_lossy_rollback():
    before = [("due", "0006_merge_20261027_0156")]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes("due")
    executor.migrate(before)
    try:
        old = executor.loader.project_state(before).apps.get_model("due", "EffectAttempt")
        legacy = old.objects.create(work_model="tests.legacy", work_id=7, claim_token=uuid4(), desired_revision=1)
        MigrationExecutor(connection).migrate(latest)
        current = EffectAttempt.objects.get(pk=legacy.pk)
        assert current.receipt is None and not current.receipt_pending
        # Old writers still work against the expanded schema.
        old.objects.create(work_model="tests.legacy", work_id=8, claim_token=uuid4(), desired_revision=1)
        current.receipt = {"members": ["accepted"], "result": []}
        current.save(update_fields=["receipt"])
        with pytest.raises(IrreversibleError, match="inline receipt"):
            MigrationExecutor(connection).migrate(before)
        current.refresh_from_db()
        assert current.receipt == {"members": ["accepted"], "result": []}
        # An adopter can copy evidence back before removing inline storage.
        current.receipt = None
        current.save(update_fields=["receipt"])
        MigrationExecutor(connection).migrate(before)
        assert old.objects.count() == 2
    finally:
        MigrationExecutor(connection).migrate(latest)
