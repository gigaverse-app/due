# 3. Keep an external view current

[Previous: own the result](native-work-tutorial.md) · [All chapters](tutorial.md)

Your app stores a book title in PostgreSQL and copies it to a search index.
Changing the title should eventually change what people find in search. The
index accepts ordinary writes, so a delayed request can put an old title back.
Use `Projection` to keep the current desired value owed until the view is repaired.

A **projection** is an external copy of application data, such as a search entry.
`Projection` is Due's capability base for maintaining that copy. We still define
an owner model, a work model and hooks, as in [chapter 2](native-work-tutorial.md).
The new behavior is repair when an earlier external write may arrive late.

The provider in this chapter is a local SQLite table. A write replaces a title;
a read returns it. There is no search service to configure. Use the
[prepared native shell](tutorial.md#prepare-the-native-chapters-once).

## 1. Define what the work captures

`CatalogEntry` and `CatalogWork` are our example models. The first stores what the
application wants; the second captures exactly what to copy. The `owner` relation,
`work_owner_field` declaration and `durable_work_schema()` use the
[work-model pattern from chapter 2](native-work-tutorial.md#1-define-the-application-record-and-its-work-row):

```python
from typing import ClassVar
from uuid import uuid4

from django.db import models
from due.django.public import UniverseDurableWork, durable_work_schema


class CatalogEntry(models.Model):
    identity = models.UUIDField(default=uuid4, unique=True, editable=False)
    title = models.CharField(max_length=100)
    author = models.CharField(max_length=100)
    published_title = models.CharField(max_length=100, blank=True)
    indexing_failed = models.BooleanField(default=False)


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

Due calls `write_projection(work)` to write the external copy, then
`on_projected(work)` to update local application state after its completion
checks. The generic parameters in `Projection[CatalogEntry, CatalogWork]` name
the owner and work models. There is no separate returned-result type here.

`search` is our example's [SQLite adapter](../../examples/books/search.py).
Its `IndexEntry` value contains `key`, `title` and `author`; `put()` writes those
values, `publish()` makes the entry visible, and `get()` reads it back. These are
example operations. The binding uses Due's `input_fields` to name the captured
fields and `DurableWorkPolicy` to configure recovery timing and retained work,
explained below the code. Here are the two central hooks from
[effects.py](../../examples/catalog/effects.py):

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

There are two new configuration choices in this binding:

| Due argument | Meaning here |
| --- | --- |
| `input_fields=(...)` | Declares which model fields make up the requested input. A change to one of them advances the desired revision. This includes the inherited `universe` field; `"example"` below is just this app's context label. |
| `policy=DurableWorkPolicy(...)` | Sets execution and recovery behavior. `retention=None` disables automatic pruning of completed work rows, keeping the current input available for repair. `repair_interval` sets when another owed repair becomes eligible. |

The retained work and provider-attempt evidence let Due deal with old calls that
may still land. Keep the registered recovery scheduler running.

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
and our `save_and_project_entry()` command, which tries completion after committing.
It uses Due's `complete_or_defer()` helper; [chapter 8](composition-tutorial.md#2-try-short-work-inline-while-retaining-recovery)
shows that function's definition and explains inline completion. In the shell,
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
