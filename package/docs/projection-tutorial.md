# 3. Keep an external view current

[Previous: own the result](native-work-tutorial.md) · [All chapters](tutorial.md)

Your app stores a book title in PostgreSQL and copies it to a search index.
Changing the title should eventually change what people find in search. A copy
of your data kept in another system like this is called a
[projection](vocabulary.md#projection-and-repair). Caches, CDNs and analytics
stores are projections too.

Chapter 2's guard protects *your* database: an outdated attempt cannot save its
answer to the product row. It cannot protect the search index. An old write is
a request that may already be on its way; by the time it lands, a newer title
may already be in the index, and the index simply keeps whichever write arrives
last. The user then searches for the new title and finds the old one.

`Projection` handles that case. It keeps the current title owed, remembers which
older writes might still land, and writes the current value again (a
**repair**) until it can be sure no older write is outstanding.

The provider in this chapter is a local SQLite table. A write replaces a title;
a read returns it. There is no search service to configure. Use the
[prepared native shell](tutorial.md#prepare-the-native-chapters-once).

## 1. Define what the work captures

The product is `CatalogEntry`, a plain Django model. The work model has the same
shape as chapter 2's `CalculationWork`; its captured input is the exact values to
copy:

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


# Same Due base and owner link as chapter 2; index_key, title and author are captured input.
class CatalogWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(CatalogEntry, on_delete=models.SET_NULL, null=True)
    index_key = models.CharField(max_length=64)
    title = models.CharField(max_length=100)
    author = models.CharField(max_length=100)

    class Meta:
        indexes, constraints = durable_work_schema("catalog_projection")
```

Both models are already in [models.py](../../examples/catalog/models.py).
`published_title` records which title the index last confirmed. The same
index key survives title changes, because we are updating one external entry.
`UniverseDurableWork`, `work_owner_field`, the one-to-one `owner` and
`durable_work_schema()` mean exactly what they did in
[chapter 2](native-work-tutorial.md#the-work-row-is-a-django-model-built-on-a-due-base-class).

## 2. Define the projection hooks

A projection capability has the same two halves as chapter 2: one hook writes to
the external system, the other records the result in your database. `search` is
this example's stand-in for a search service: a module that reads and writes a
local SQLite table. Here are the two central hooks from
[effects.py](../../examples/catalog/effects.py):

```python
from datetime import timedelta

from due.django.public import DurableWorkPolicy, Projection
from examples.books import search
from examples.catalog.models import CatalogEntry, CatalogWork


# Due base: like ReplaySafeEffect, plus tracking of old writes that may still land, and repairs.
class CatalogProjection(Projection[CatalogEntry, CatalogWork]):
    # The effect: copy the captured values to the external index. It may run more than once.
    def write_projection(self, work: CatalogWork) -> None:
        search.put(search.IndexEntry(key=work.index_key, title=work.title, author=work.author))
        search.publish(work.index_key)

    # Settlement, like finalize_success(): runs only after Due's token and revision check.
    def on_projected(self, work: CatalogWork) -> None:
        CatalogEntry.objects.filter(pk=work.owner_id).update(published_title=work.title, indexing_failed=False)


project_catalog = CatalogProjection.for_owner(
    work=CatalogWork,
    # The fields Due compares to decide whether a request is new input; must include "universe".
    input_fields=("universe", "index_key", "title", "author"),
    # Never prune this work row (late writes refer to it); make repair eligible every 5 seconds.
    policy=DurableWorkPolicy(retention=None, repair_interval=timedelta(seconds=5)),
)
```

| Code | Comes from | What it does |
| --- | --- | --- |
| `Projection[CatalogEntry, CatalogWork]` | Due | The capability base for keeping an external copy current. Type parameters: the owner model and the work model. Writes may repeat, as with `ReplaySafeEffect`; in addition, Due tracks earlier writes that may still land and schedules repairs. |
| `write_projection(self, work)` | A hook you implement | The effect: write the captured values to the external system. Like `perform_effect()`, it runs outside a transaction and may run more than once. |
| `on_projected(self, work)` | A hook you implement | Settlement: record locally that the current value was written. Like `finalize_success()`, Due calls it only after checking that the attempt is current. |
| `search.put()`, `search.publish()`, `search.get()` | This example | Write, publish and read an entry in the local SQLite "search index". |
| `input_fields=(...)` | Due | Names the work fields that count as captured input: Due compares these to decide whether a request changes the input and needs a new revision. It must include `"universe"`. |
| `DurableWorkPolicy(...)` | Due | The policy object. In chapter 2, `for_owner()` used the standard one; here it is passed explicitly to change two settings. |
| `retention=None` | Due policy setting | Never delete this work row once it completes (the standard policy deletes completed rows after seven days). Records of earlier writes refer to it, so a projection requires this. |
| `repair_interval=timedelta(seconds=5)` | Due policy setting | How soon another repair pass becomes eligible. When it actually runs also depends on recovery's schedule. Five seconds keeps the demo short; the standard interval is five minutes. |

The prepared class also marks the product as failed when its
[retry budget](vocabulary.md#retry-budget) is exhausted. Keep Beat running:
recovery is what carries out the repairs.

## 3. Save and project a title

The application command uses the same `atomic()` and `request_work()` pattern
as [chapter 2](native-work-tutorial.md#3-request-the-work-together-with-the-product-change).
`universe="example"` sets the tenant label; the examples set one so you can see
where it goes, and a single-tenant app leaves it out:

```python
with project_catalog.atomic(entry_id) as pending:
    entry = pending.locked_owner
    entry.title, entry.author = title, author
    entry.save(update_fields=["title", "author"])
    # universe is the tenant label from chapter 2; a single-tenant app leaves it out.
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

# Commits the change and its work, then tries the projection at once (chapter 8 shows how).
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
