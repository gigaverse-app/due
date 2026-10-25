# 7. Recover cleanup independently

[Previous: observation](observation-tutorial.md) · [All chapters](tutorial.md)

A function creates temporary output, then removes it when finished. If its process
dies between those calls, a Python `finally` block cannot finish cleanup.
`IndependentCompensation` records the cleanup obligation **before** the call that
can create the output, so another worker can perform it later.

**Compensation** is an application-defined follow-up action for an earlier effect;
here it means deleting temporary output. It does not roll back an external system's
transaction. **Independent** means the cleanup has its own work row and can recover
even while the original work is incomplete or has been replaced.

The operation here copies text into a local SQLite record and clears its temporary
copy. We reuse the prepared Printworks renderer, but this chapter only needs its
render and cleanup functions. No PDF generation or external service is involved.

## 1. Give cleanup its own work row

The renderer uses one temporary key per execution attempt. Cleanup keeps that key
even if the product requests a newer attempt.

`CleanupWork` is our example model and `spool_key` is our name for the temporary
output's identifier. The Due base and schema helper are the
[work-model declarations introduced in chapter 2](native-work-tutorial.md#1-define-the-application-record-and-its-work-row).

```python
from django.db import models
from due.django.public import UniverseDurableWork, durable_work_schema


class CleanupWork(UniverseDurableWork):
    spool_key = models.CharField(max_length=200)

    class Meta:
        indexes, constraints = durable_work_schema("acme_cleanup")
```

This model is supplied in [models.py](../../examples/acme_printworks/acme/models.py).
It has no product-owner foreign key: the temporary output remains owed cleanup
independently of the product's current desired revision.

## 2. Capture the key and define cleanup

The **producer** is the operation that creates the temporary output. Its example
work model is `ProofWork`, which captures text and a revision ID for a `Job` owner.
`IndependentCompensation[ProofWork, CleanupWork]` names the producer and cleanup
work models, respectively. Unlike the owned capabilities, its type parameters do
not name an application owner or a returned result.

Due calls `capture()` to construct cleanup input for one producer attempt and
`compensate()` to perform that cleanup. `WorkClaim` is Due's description of that
attempt, including its unique `token`. Our `providers.cleanup(key)` function clears
the simulator's temporary copy and reports whether it is conclusively finished.

We configure this capability directly because cleanup has no application owner.
`registered_nudge(CleanupWork)` supplies Due's worker-notification callback for
that registered model. `inspection_interval` controls rechecks of inconclusive
cleanup. `retention=None` retains completed work rows, and `max_rearms=None`
allows failed cleanup attempts to be retried without a finite rearm limit.

These are the cleanup hooks from [effects.py](../../examples/acme_printworks/acme/effects.py):

```python
from datetime import timedelta

from due.django.public import (
    DurableWorkPolicy,
    IndependentCompensation,
    WorkClaim,
    registered_nudge,
)
from acme import providers
from acme.models import CleanupWork, ProofWork


class SpoolCleanup(IndependentCompensation[ProofWork, CleanupWork]):
    def capture(self, producer: WorkClaim[ProofWork]) -> CleanupWork:
        return CleanupWork(spool_key=str(producer.token), universe="acme")

    def compensate(self, work: CleanupWork) -> bool:
        return providers.cleanup(work.spool_key)


cleanup_spool = SpoolCleanup(
    producer_model=ProofWork,
    model=CleanupWork,
    nudge=registered_nudge(CleanupWork),
    policy=DurableWorkPolicy(retention=None, max_rearms=None),
    inspection_interval=timedelta(seconds=5),
)
```

`capture()` runs before the producer calls the provider. It derives the cleanup
key from the attempt token, so cleanup can identify output even when the producer
never receives a response. The cleanup capability is registered for ordinary
worker delivery and recurring recovery alongside the producer.

`compensate()` must report whether cleanup is conclusively finished. Here the
provider returns `True` only when the temporary data is cleared **and** its render
has a terminal marker proving that attempt cannot write more data. Missing data
alone returns `False`: a delayed write could still arrive.

## 3. Protect the call that creates output

`OwnedDurableWorkCapability` is Due's more general base for owned work. The
`ReplaySafeEffect` base from chapter 2 builds on it and supplies a simpler
`perform_effect()` interface. Here we implement `apply(work, context)` so we can
use the execution context directly.

`WorkExecutionContext` is a Due object describing this attempt's claim and lease,
with methods for checking ownership before external calls. Due supplies it when
calling `apply()`; application code does not construct a new context for each call.
`record_cleanup_before_call(context)` uses it to reserve cleanup for this exact
attempt. The returned `output` is a Due `ProtectedOutput` handle; calling
`confirm_compensated()` records that cleanup was proven complete.

`Job`, `ProofWork`, `PublishProof` and `render_proof()` are example code. Our provider
function `render(text, proof_key, spool_key)` stores the result text and a temporary
copy. The two keys serve different purposes: Due's `provider_idempotency_key()`
identifies the requested revision across retries, while `fencing_token` identifies
this particular attempt's temporary output. It matches the claim token used by
`capture()` above. The core of the prepared producer is:

```python
from due.django.public import OwnedDurableWorkCapability, WorkExecutionContext
from acme.models import Job, ProofWork


def render_proof(work: ProofWork) -> str:
    return providers.render(work.text, work.provider_idempotency_key(), str(work.fencing_token))


class PublishProof(OwnedDurableWorkCapability[Job, ProofWork, str]):
    def apply(self, work: ProofWork, context: WorkExecutionContext[ProofWork]) -> str:
        with cleanup_spool.record_cleanup_before_call(context) as output:
            key = render_proof(work)
            if providers.cleanup(str(work.fencing_token)):
                output.confirm_compensated()
            return key

    def finalize_success(self, work: ProofWork, proof_key: str) -> None:
        Job.objects.filter(pk=work.owner_id).update(published_key=proof_key, proof_failed=False)
```

`record_cleanup_before_call()` commits the cleanup reservation before entering
the body. If the process dies inside the body, the reservation survives.
`confirm_compensated()` closes it only after ordinary execution proves cleanup
finished. An expired producer reservation lets independent recovery inspect and
clean the output.

The prepared producer also hands its published key to a projection, using the
transactional handoff taught in chapter 8. Its binding and execution-time policy
are already configured in the example; the snippets above focus on the cleanup
boundary.

## 4. Run once and inspect the cleanup

From the repository root, prepare the example's dedicated local services and run
its shell with the environment installed in chapter 1:

```bash
cd examples/acme_printworks
unset DJANGO_SETTINGS_MODULE
docker compose up -d --wait
../../.venv/bin/python manage.py migrate
../../.venv/bin/python manage.py shell
```

If you use existing dedicated services, set `PGHOST`, `PGPORT`, `PGDATABASE`,
`PGUSER`, `PGPASSWORD` and `CELERY_BROKER_URL` instead of starting Compose.

Request a result using the prepared product commands, then run the producer:

```python
from due.django.conformance import hold_nudges
from acme import providers
from acme.effects import publish_proof
from acme.models import CleanupWork, Job
from acme.services import add_revision, request_proof

with hold_nudges():
    job = Job.objects.create()
    revision = add_revision(job.pk, 1, "Hello from Due")
    work = request_proof(revision.pk)
    publish_proof.attempt_execution(work.pk)

job.refresh_from_db()
cleanup = CleanupWork.objects.latest("pk")
print(providers.proof_text(job.published_key))  # Hello from Due
print(cleanup.state)  # succeeded
with providers.database() as connection:
    spool = connection.execute("SELECT spool FROM renders WHERE key = ?", (cleanup.spool_key,)).fetchone()[0]
print(spool)  # None
```

The [product commands](../../examples/acme_printworks/acme/services.py) create an
immutable text revision and admit `ProofWork` for it. The provider writes the text,
ordinary cleanup removes its temporary copy, and `confirm_compensated()` closes
the cleanup obligation. Keep separate workers stopped during this exercise.

## 5. Interrupt the producer before cleanup

Exit the shell and run the focused test from the same directory:

```bash
../../.venv/bin/python -m pytest tests/test_story.py::test_independent_cleanup_survives_process_death -v
```

Tests create a separate test database and isolate the provider's SQLite file.

The test starts a child process, writes real temporary output, and exits that
process immediately before cleanup. It checks these boundaries in order:

```text
Child exited at the injected crash point.
Temporary output exists; cleanup work exists.
Cleanup recovery removes the temporary output and succeeds.
The product still has no published result.
Producer recovery runs separately and publishes the result.
```

Those are assertions against the provider records and PostgreSQL rows. Cleanup
can succeed before the original product work completes. Run the accompanying
absence check to see why "nothing found" is insufficient evidence:

```bash
../../.venv/bin/python -m pytest tests/test_story.py::test_absent_spool_is_not_completion_evidence -v
```

Stop these disposable services with `docker compose down` when finished, and
return to the repository root for the next chapter:

```bash
cd ../..
export DJANGO_SETTINGS_MODULE=examples.settings
```

[Next: compose and test your work](composition-tutorial.md)
