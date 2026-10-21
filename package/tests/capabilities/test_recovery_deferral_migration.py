"""Step identities preserve old holds on upgrade and safely coalesce them on rollback."""

from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)


def test_step_migration_preserves_existing_holds_and_reversible_advisory_state():
    before = [("due", "0002_effectattempt_target_retired")]
    after = [("due", "0003_recoverydeferral_step")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    try:
        old = executor.loader.project_state(before).apps.get_model("due", "RecoveryDeferral")
        now = timezone.now()
        original = old.objects.create(effect="migration", phase="discovery", identity="7", retry_at=now)
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        current = executor.loader.project_state(after).apps.get_model("due", "RecoveryDeferral")
        assert current.objects.get(pk=original.pk).step == ""
        later = now + timedelta(hours=1)
        current.objects.create(
            effect="migration", phase="discovery", step="cleanup", identity="7", retry_at=later, failures=4
        )
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        restored = executor.loader.project_state(before).apps.get_model("due", "RecoveryDeferral")
        hold = restored.objects.get(effect="migration", phase="discovery", identity="7")
        assert hold.pk == original.pk
        assert hold.retry_at == later and hold.failures == 4
    finally:
        MigrationExecutor(connection).migrate(after)
