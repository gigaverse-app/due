from pathlib import Path
from unittest.mock import patch

import pytest

from due.django.conformance.delivery import WorkTableDelivery
from due.django.testing import hold_nudges
from examples.books import search
from examples.catalog.effects import project_catalog
from examples.catalog.models import CatalogEntry
from examples.catalog.services import save_entry

pytestmark = pytest.mark.django_db(transaction=True)


def test_old_external_write_cannot_publish_old_product_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUE_BOOK_INDEX", str(tmp_path / "index.sqlite3"))
    with hold_nudges():
        work = save_entry("First edition", "Author")
        original = search.put
        changed = False

        def delayed(entry: search.IndexEntry) -> str:
            nonlocal changed
            if not changed:
                changed = True
                save_entry("Second edition", "Author", entry_id=work.owner_id)
                assert project_catalog.attempt_execution(work.pk)
                assert CatalogEntry.objects.get(pk=work.owner_id).published_title == "Second edition"
            return original(entry)

        # The provider applies the captured first request after revision 2.
        with patch.object(search, "put", delayed):
            assert not project_catalog.attempt_execution(work.pk)
    assert CatalogEntry.objects.get(pk=work.owner_id).published_title == "Second edition"
    assert search.search("First edition") == [work.index_key]
    with WorkTableDelivery(project_catalog).session() as recovery:
        recovery.recover()
    assert search.search("First edition") == []
    assert search.search("Second edition") == [work.index_key]
    assert CatalogEntry.objects.get(pk=work.owner_id).published_title == "Second edition"
