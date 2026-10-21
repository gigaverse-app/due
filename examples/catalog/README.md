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

Add `--inline` to try the projection immediately after admission commits. Inside an
enclosing transaction it defers; a held claim leaves recovery responsible. The
generated caller histories repeat the original entry ID after creating it, and
exercise refusal, lost responses, transaction rollback and a competing claim.

If the external index loses an entry, restore it from the retained projection
without editing the book:

```bash
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django refresh_catalog_entry 1
```

The command commits a fresh wake with `notify_worker=False`, then tries completion
inline. A successful refresh emits no competing worker notification. If the
provider fails or the process dies, the persisted obligation remains available
to registered recovery. Inside an enclosing transaction, completion defers until
commit; rollback cancels the refresh. Each invocation is an explicit request to
rewrite the external entry, even if the previous refresh already succeeded.

Run the controlled old-write and refresh histories:

```bash
uv run pytest integration_tests/examples/test_catalog.py integration_tests/examples/test_catalog_refresh.py integration_tests/conformance/test_catalog_contract.py -v
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
