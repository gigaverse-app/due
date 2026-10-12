from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from examples.scheduled.models import Publication

pytestmark = pytest.mark.django_db(transaction=True)


def test_existing_publications_gain_distinct_keys_without_new_rows() -> None:
    before = [("scheduled", "0001_initial")]
    after = [("scheduled", "0002_submission_identity")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    try:
        old_publication = executor.loader.project_state(before).apps.get_model("scheduled", "Publication")
        at = timezone.now() + timedelta(days=1)
        originals = [old_publication.objects.create(text=f"Legacy {i}", publish_at=at).pk for i in range(2)]
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        rows = list(Publication.objects.order_by("pk"))
        assert [row.pk for row in rows] == originals
        assert len({row.submission_key for row in rows}) == 2
        assert all(row.initial_publish_at == row.publish_at == at for row in rows)
    finally:
        MigrationExecutor(connection).migrate(after)
