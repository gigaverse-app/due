# Document publication: the Procrastinate-to-Due port

This application originated as a Procrastinate demo and was ported to Due. It is
our concrete Procrastinate migration example: the domain remains document
publication, while Due now owns captured work, execution recovery and fenced
publication of the result.

The current checkout contains the **Due implementation**, not a runnable copy of
the historical Procrastinate version. The [Procrastinate migration guide](../../docs/migrations/procrastinate.md)
explains the source interfaces and the port's semantic boundaries. Its source
snippets illustrate those interfaces; they are not an archived upstream snapshot.

## Run and inspect the port

Follow [Run the example](../../README.md#run-the-example) for PostgreSQL, Redis,
migrations, the worker, Beat and a publication request.

- [models.py](models.py) defines the document and its captured publication work.
- [effects.py](effects.py) separates file publication, local completion and admission.
- [apps.py](apps.py) registers the work and recurring recovery.
- [Application contract](../../tests/conformance/test_document_contract.py) and
  [its bindings](../../tests/conformance/document_examples.py) exercise the real
  producer and provider, including process death and changed inputs.

The obligation is the document's current publication, not an append-only job
history. Repeating equal inputs preserves the existing outcome; changed text
starts a new revision. Immutable file keys make retries safe, and Due fences
which revision can publish the document's current pointer.

Additional migration comparisons should build on this port as separate add-ons.
They must identify their own source baseline and limitations rather than relabel
this application's origin or replace its existing proof suite.
