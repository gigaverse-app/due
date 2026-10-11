from datetime import timedelta

from due.django.capabilities.projection import Projection
from due.django.public import DurableWorkPolicy, DurableWorkState
from examples.books import search
from examples.catalog.models import CatalogEntry, CatalogWork


class CatalogProjection(Projection[CatalogEntry, CatalogWork]):
    @property
    def uncertain_repair_interval(self) -> timedelta:
        return timedelta(seconds=5)

    def write_projection(self, work: CatalogWork) -> None:
        # This provider has no revision fence: an already-sent older write can
        # overwrite it. Due retains evidence and owes repair, not remote cancellation.
        search.put(search.IndexEntry(key=work.index_key, title=work.title, author=work.author))
        search.publish(work.index_key)

    def on_projected(self, work: CatalogWork) -> None:
        CatalogEntry.objects.filter(pk=work.owner_id).update(published_title=work.title, indexing_failed=False)

    def finalize_retry(self, work: CatalogWork, error: Exception) -> None:
        if work.state == DurableWorkState.EXHAUSTED:
            CatalogEntry.objects.filter(pk=work.owner_id).update(indexing_failed=True)


project_catalog = CatalogProjection.for_owner(
    owner=CatalogEntry,
    work=CatalogWork,
    input_fields=("universe", "index_key", "title", "author"),
    policy=DurableWorkPolicy(retention=None),
)
