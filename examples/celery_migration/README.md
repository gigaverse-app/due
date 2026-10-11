# Celery → Due: an add-on to document publication

This comparison extends the existing [document demo](../documents/README.md).
It adds a plain-Celery producer/task and reuses the same `Document` model and
[file provider](../documents/storage.py). The native producer and capability
remain in [documents/effects.py](../documents/effects.py). It is separate from
the [Books port of Procrastinate's demo](../books/README.md).

The Celery baseline is newly written comparison code, not an upstream application
or a claim about every Celery deployment. It uses post-commit publication,
bounded OSError retries, late acknowledgements and worker-loss rejection. It
has no custom outbox, stale-write check or recovery sweep. If your application
already supplies those, compare that implementation instead.

## Run the same operation on both paths

From the repository root, follow the [shared setup](../../README.md#run-the-example)
through installing dependencies and applying migrations. In a worker terminal:

```bash
export DJANGO_SETTINGS_MODULE=examples.settings
uv run celery -A examples.celery_app:app worker --loglevel=INFO -Q due \
  --include examples.celery_migration.tasks
```

For Due's recovery, also run Beat as in the shared setup. The optional `--include`
loads the baseline; ordinary example workers need no change. In another terminal:

```bash
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django shell <<'PY'
from examples.documents.models import Document
from examples.celery_migration.tasks import request_publication as celery_publish
from examples.documents.effects import request_publication as due_publish

before = Document.objects.create()
after = Document.objects.create()
operation_key = celery_publish(before.pk, "Same publication")
work = due_publish(after.pk, "Same publication")
print("Inspect these document IDs:", before.pk, after.pk)
PY
```

After the worker finishes, inspect those two rows and their files:

```python
from pathlib import Path
from examples.documents.models import Document

for document_id in (before_id, after_id):  # Substitute the two printed IDs.
    document = Document.objects.get(pk=document_id)
    assert document.published_key
    assert (Path("/tmp/due-documents") / document.published_key).read_text() == document.text
```

Use your configured `DUE_OUTPUT_DIR` instead if you changed it. Both workers must
see the same filesystem. The keys intentionally differ: compare bytes and product
completion, not identical generated IDs. Use separate document rows for the two
engines; this demonstration is not a protocol for jointly owning one occurrence.

## What changes in the migration

| Boundary | Plain Celery baseline | Existing native Due app |
| --- | --- | --- |
| Producer | Save text, register `.delay()` with `transaction.on_commit` | Save text and `pending.request_work()` in `publish_document.atomic()` |
| Captured input | Task args: document ID, text, stable operation key | `PublicationWork.text`, owner and admission/revision identity |
| Provider | `storage.write_revision(text, operation_key)` | The same function, using `work.provider_idempotency_key()` |
| Product completion | ORM update inside the task | `finalize_success()` under Due's current revision/token fence |
| Failure/retry | Celery delivery options and bounded exception retries | Persisted work lifecycle, policy and registered recovery |
| Return to producer | An application operation key, not a result handle | A work row, not completed publication or an `AsyncResult` |

Read [tasks.py](tasks.py) beside [effects.py](../documents/effects.py). The provider
logic does not change. The substantive move is the ORM update out of the task
body and into fenced settlement, plus the work record committed with intent.
Putting the whole old task inside `perform_effect()` would miss the completion
fence.

Equal submissions are another semantic change: the baseline creates independent
messages, whereas Due preserves the current obligation for equal inputs. A newer
text starts a Due revision. For append-only publication events, use a separate
request owner instead of this document's one-to-one work row.

## Run the evidence

```bash
uv run pytest tests/examples/test_celery_migration.py \
  tests/integration/test_celery_migration.py \
  tests/conformance/test_document_contract.py \
  tests/conformance/test_document_process_control.py \
  tests/integration/test_celery_worker.py -v -rx
```

The baseline deliberately has **two strict expected failures**. Their assertions
state the desired product invariants; they are not passing tests that assert a
broken outcome. A missing fault injection is an error, and fixing either baseline
gap without updating its declaration is an XPASS failure.

| History | Baseline evidence | Due evidence already owned by the document app |
| --- | --- | --- |
| Ordinary delivery | Same bytes and product pointer; real Redis/prefork worker | Existing real Redis/prefork journey |
| Product transaction rolls back | No message and no text change | Generated producer rollback histories |
| Worker stops after writing file | Explicit redelivery reuses the same key and completes | Generated actual child-process deaths recover through registered work |
| Initial delivery is lost | Expected failure: no executable obligation remains | Generated recovery and real worker lost-notification test |
| A finishes after newer B | Expected failure: A overwrites B's local pointer | Existing generated snapshot/supersession histories |

The baseline death control stops execution at the actual file-write boundary and
then explicitly redelivers the message. It proves application replay safety; it
does not prove Redis acknowledgement behavior after an OS-killed prefork child.
The Due child-process histories exercise actual process exit. These are different
levels of evidence, not an identical infrastructure-fault benchmark.

The existing Due suite is reused rather than copied. Both paths share real
PostgreSQL, the file writer and the product model. No comparison claims higher
throughput, lower latency or exactly-once external execution.

## Is the migration worth it here?

The demonstrated gains are recovery after a lost wake-up and rejection of stale
local completion. Safe repeat writes are shared ground, supplied by the storage
contract on both sides. This small baseline has no custom recovery machinery to
remove, so the migration **adds** a work model, schema and registration. Its value
is stronger product behavior, not fewer lines. Celery remains the transport, and
Due additionally needs periodic recovery and work-state monitoring.

If immutable bytes plus task retries already meet your requirement, staying on
plain Celery is reasonable. A payment, email or non-idempotent provider needs a
different example and an evidence-backed capability; this file demo cannot prove
that migration safe.
