"""An operator can restore a missing catalog index entry without editing its source."""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction

from due.django.conformance.delivery import WorkTableDelivery
from due.django.testing import WorkerDied, hold_nudges
from examples.books import search
from examples.catalog.effects import project_catalog
from examples.catalog.models import CatalogEntry
from examples.catalog.services import save_and_project_entry
from integration_tests.examples.books_support import index_environment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("failure", [None, "rejected", "worker_died"])
def test_refresh_catalog_restores_the_index_with_recovery_after_interruption(failure: str | None) -> None:
    with index_environment() as provider:
        work = save_and_project_entry("First edition", "Author")
        with search.connection() as connection:
            connection.execute("DELETE FROM books WHERE key = ?", (work.index_key,))
        assert search.get(work.index_key) is None

        # The provider is the only fault seam; the real command records the wake.
        with hold_nudges() as held:
            if failure:
                fault = (
                    patch.object(search, "put", side_effect=WorkerDied("process interrupted"))
                    if failure == "worker_died"
                    else provider.writes.reject(lambda: OSError("index unavailable"))
                )
                with fault, pytest.raises(WorkerDied if failure == "worker_died" else CommandError):
                    call_command("refresh_catalog_entry", work.owner_id, stdout=StringIO())
            else:
                call_command("refresh_catalog_entry", work.owner_id, stdout=StringIO())
            assert not held, "The inline refresh must not also enqueue a competing worker"

        if failure:
            assert search.get(work.index_key) is None
            with WorkTableDelivery(project_catalog).session() as recovery:
                recovery.recover()
        entry = search.get(work.index_key)
        assert entry is not None and entry.published
        assert (entry.title, entry.author) == ("First edition", "Author")
        owner = CatalogEntry.objects.get(pk=work.owner_id)
        assert (owner.title, owner.author, owner.published_title) == ("First edition", "Author", "First edition")


@pytest.mark.parametrize("rollback", [False, True])
def test_refresh_catalog_respects_an_enclosing_transaction(rollback: bool) -> None:
    with index_environment() as provider:
        work = save_and_project_entry("First edition", "Author")
        with search.connection() as connection:
            connection.execute("DELETE FROM books WHERE key = ?", (work.index_key,))
        calls_before = provider.writes.total
        with hold_nudges() as held:
            with transaction.atomic():
                call_command("refresh_catalog_entry", work.owner_id, stdout=StringIO())
                assert provider.writes.total == calls_before
                assert not held
                if rollback:
                    transaction.set_rollback(True)
            assert len(held) == int(not rollback)
        with WorkTableDelivery(project_catalog).session() as recovery:
            recovery.recover()
        entry = search.get(work.index_key)
        if rollback:
            assert entry is None
        else:
            assert entry is not None and entry.published
            assert entry.title == "First edition"
