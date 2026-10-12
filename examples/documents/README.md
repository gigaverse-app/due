# Document publication: a minimal Due application

This small application writes immutable files and publishes their keys through
fenced Django completion. It is the focused reference used in the migration
guides' code snippets.

The repository's Procrastinate-based application is the separate
[Books demo](../books/README.md), which preserves Procrastinate's
create → index → mark-indexed journey and credits its upstream source. Keep that
provenance distinct from this smaller publication example.

Follow [Run the example](../../README.md#run-the-example) for setup.
[models.py](models.py), [effects.py](effects.py) and the shared
[registry](../registry.py) contain the runtime implementation.
The [application contract](../../integration_tests/conformance/test_document_contract.py)
executes its producer, file writes, recovery and product completion.

The obligation is the document's current publication, not an append-only job
history. Equal inputs preserve the existing outcome; changed text starts a new
revision. Immutable file keys make retries safe, and Due fences which revision
can publish the current pointer. Additional queue comparisons should be separate
add-ons and retain these semantics and the existing proof suite.

## Celery migration add-on

The [Celery comparison](../celery_migration/README.md) adds a plain task/producer
alongside this native implementation. Both share [storage.py](storage.py); it
does not replace the default demo or its conformance suite.
