from django.db import transaction

from examples.catalog.effects import project_catalog
from examples.catalog.models import CatalogEntry, CatalogWork


@transaction.atomic
def save_entry(title: str, author: str, *, entry_id: int | None = None, notify_worker: bool = True) -> CatalogWork:
    if entry_id is None:
        entry_id = CatalogEntry.objects.create(title=title, author=author).pk
    with project_catalog.atomic(entry_id, notify_worker=notify_worker) as pending:
        entry = pending.locked_owner
        entry.title, entry.author = title, author
        entry.full_clean()
        entry.save(update_fields=["title", "author"])
        return pending.request_work(
            CatalogWork(index_key=str(entry.identity), title=title, author=author, universe="example")
        )


def retry_entry(entry_id: int) -> bool:
    with project_catalog.atomic(entry_id) as pending:
        return pending.retry_failed()


def save_and_project_entry(title: str, author: str, *, entry_id: int | None = None) -> CatalogWork:
    """Commit the requested title before trying the index; recovery owns any unfinished projection."""
    work = save_entry(title, author, entry_id=entry_id, notify_worker=False)
    project_catalog.complete_or_defer(work)
    return work
