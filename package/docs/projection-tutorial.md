# 3. Keep an external view current

[Previous: own the result](native-work-tutorial.md) · [All chapters](tutorial.md)

Your app stores a book title in PostgreSQL and copies it to a search index.
Changing the title should eventually change what people find in search. The
index accepts ordinary writes, so a delayed request can put an old title back.
Use `Projection` to keep the current desired value owed until the view is repaired.

The provider in this chapter is a local SQLite table. A write replaces a title;
a read returns it. There is no search service to configure. Use the
[prepared native shell](tutorial.md#prepare-the-native-chapters-once).

## 1. Define what the work captures

The product is `CatalogEntry`, with `title`, `author` and a stable `identity`.
The work captures the exact values to copy:

```python
from typing import ClassVar

from django.db import models
from due.django.public import UniverseDurableWork, durable_work_schema


class CatalogWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(CatalogEntry, on_delete=models.SET_NULL, null=True)
    index_key = models.CharField(max_length=64)
    title = models.CharField(max_length=100)
    author = models.CharField(max_length=100)

    class Meta:
        indexes, constraints = durable_work_schema("catalog_projection")
```

This model is already in [models.py](../../examples/catalog/models.py). The same
index key survives title changes, because we are updating one external entry.

## 2. Define the projection hooks

Here are the two central hooks from [effects.py](../../examples/catalog/effects.py):

```python
from datetime import timedelta

from due.django.public import DurableWorkPolicy, Projection
from examples.books import search
from examples.catalog.models import CatalogEntry, CatalogWork


class CatalogProjection(Projection[CatalogEntry, CatalogWork]):
    def write_projection(self, work: CatalogWork) -> None:
        search.put(search.IndexEntry(key=work.index_key, title=work.title, author=work.author))
        search.publish(work.index_key)

    def on_projected(self, work: CatalogWork) -> None:
        CatalogEntry.objects.filter(pk=work.owner_id).update(published_title=work.title, indexing_failed=False)


project_catalog = CatalogProjection.for_owner(
    work=CatalogWork,
    input_fields=("universe", "index_key", "title", "author"),
    policy=DurableWorkPolicy(retention=None, repair_interval=timedelta(seconds=5)),
)
```

`write_projection()` writes the external view. `on_projected()` updates local
product state under Due's completion checks. The prepared class also marks the
product as failed when its retry budget is exhausted.

The repair policy retains the history needed to deal with old calls that may
still land. `repair_interval` controls when owed repair becomes eligible; keep
the registered recovery scheduler running.

## 3. Save and project a title

The application command uses the same admission pattern as chapter 2:

```python
with project_catalog.atomic(entry_id) as pending:
    entry = pending.locked_owner
    entry.title, entry.author = title, author
    entry.save(update_fields=["title", "author"])
    work = pending.request_work(
        CatalogWork(index_key=str(entry.identity), title=title, author=author, universe="example")
    )
```

[services.py](../../examples/catalog/services.py) includes creation, validation
and a convenience command that tries completion after committing. In the shell,
use that prepared command:

```python
from examples.books import search
from examples.catalog.services import save_and_project_entry

work = save_and_project_entry("First edition", "Author")
print(search.get(work.index_key).title)  # First edition

work = save_and_project_entry("Second edition", "Author", entry_id=work.owner_id)
print(search.get(work.index_key).title)  # Second edition
```

The product change and its captured work commit together. If immediate completion
is interrupted, recovery still has the desired title.

## 4. See an old external write get repaired

Run this controlled history from a shell at the repository root:

```bash
uv run pytest integration_tests/examples/test_catalog.py -v
```

It pauses the first provider write, publishes `Second edition`, then lets
`First edition` land late. It checks the actual SQLite index before and after
recovery:

```text
Local published title: Second edition
External index after the late write: First edition
External index after recovery: Second edition
```

These are the values the test asserts. The external view can temporarily be old.
Due preserves the repair obligation and reapplies the current desired value.

## 5. Explicitly refresh an entry

If the index loses data, you can request another projection without changing the
book:

```python
from examples.catalog.services import refresh_entry

refresh_entry(work.owner_id)
print(search.get(work.index_key).title)  # Second edition
```

That command uses `.wake()` and `.complete_or_defer()`, covered in chapter 8.
The [application contract](../../integration_tests/conformance/catalog_examples.py)
also exercises lost delivery, replay and stale writes against the actual index.

[Next: schedule, move and cancel work](scheduling-tutorial.md)
