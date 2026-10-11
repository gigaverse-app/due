# Mutable catalog: a late write still owes repair

This example uses Due's `Projection` for editable book metadata, separate from
the immutable two-stage [books workflow](../books/README.md). PostgreSQL owns the
desired title/author and the last fenced product completion. The same local
SQLite index stores a mutable entry under a stable identity.

Use the [shared setup](../books/README.md#run-it), including migrations, worker
and Beat, then create and edit an entry (replace `1` with the printed ID):

```bash
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django catalog_entry 'First edition' 'Author'
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django catalog_entry 'Second edition' 'Author' --id 1
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django shell <<'PY'
from examples.catalog.models import CatalogEntry
from examples.books import search
for entry in CatalogEntry.objects.all():
    print(entry.pk, entry.title, entry.published_title, search.get(str(entry.identity)))
PY
```

Run the controlled old-write history:

```bash
uv run pytest tests/examples/test_catalog.py tests/conformance/test_catalog_contract.py -v
```

The tests hold the old external call, change the desired title and execute the
new revision, then let the old write land. The old worker cannot change the
new product completion. The provider temporarily contains the old title; normal
registered recovery rewrites the current title. Generated histories also cover
worker death, retained uncertainty, and a negative control that removes repair.

This is **eventual repair**, not strict external ordering. This provider has no
revision fence and cannot cancel already-sent requests. A crash can leave an
unknown old attempt, so the example retains its identity and repair debt without
a finite expiry window. Production deployments must choose a provider ordering
or observation policy and monitor retained uncertainty and exhausted repairs.
