# Django Tasks with Due: start simple, grow with Acme Printworks

New to Due? Start with the [deliberately trivial greeting tutorial](../package/docs/quickstart.md)
to learn the runtime without a business application. Come back here to add product
guarantees. The [API guide](../package/docs/api-guide.md) maps the consolidated
imports to the capabilities used throughout this story.

![Due: An attempt can end. The obligation remains.](https://raw.githubusercontent.com/gigaverse-app/due/main/docs/assets/banner.png?v=e6e1fea2a5af)

First published on [DEV](https://dev.to/aviadr1/django-tasks-with-due-commit-the-work-recover-after-a-crash-n5k). This edition preserves the author’s edits and includes the runnable application and contracts.

Using `django-due` helps you start with a background task you can explain in a few lines. But it can help you grow and become more robust preventing lost or incorrect work in edge cases, that can be very hard to test for or prevent.

This tutorial shows that progression with **Due's backend for Django Tasks**, installed as `django-due`. You'll begin with a familiar `@task` and `.enqueue()`, then evolve the parts that need stronger guarantees into native Due capabilities. The payoff is practical: a small first feature, application contracts that give you reusable failure tests immediately, and coordination that expands with the product. Always build the contract alongside the feature. It is one of the main reasons to use Due.

Meet **Acme Printworks**, a fictional company building a Django application for small businesses ordering brochures. Picture its first customer, **Willow Cafe**, uploading copy for **500 brochures, job 418**. Acme initially needs a word count for an estimate. Later, Willow Cafe wants a proof, changes its opening date from June 12 to June 19, approves the revision, and sends it to a printing partner.

One premise matters for the rest of the story: **rendering a PDF can take a surprisingly long time**. Even a small document can trigger a long render with some inputs, as [WeasyPrint's rendering documentation](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#long-renderings) explains. For our story, assume Willow Cafe's troublesome PDF takes **10 minutes** to render.

Ten minutes gives the world time to change around a running task. The customer might upload a correction. A rare power outage might stop the machine, or an unexpected server restart might kill the worker seven minutes into the render. None of these needs to happen often for Acme to need an answer: which proof should the customer see, what work is still owed, and who will remove the temporary files? We'll use that same ten-minute window throughout the story.

Each chart uses the same clock as Acme adds features: r17 starts at minute 0, the customer corrects the date and font at minute 2, and the faster r18 publishes June 19 at minute 3. Then we examine two branches. A restart at minute 7 kills the old worker; without that restart, its old render returns at minute 10. These are alternative histories. A killed worker cannot also finish at minute 10. The portal and printer sections show what can still happen outside the restarted process.

We'll follow that same order through each requirement. You'll see the code change, the specific failure it must handle, and a test that makes the failure happen.

At the first stage, many task runners would work. Acme's reason to choose Due is that the same PostgreSQL-backed work model can support the later requirements without Acme inventing its own ownership, recovery, and evidence machinery. Due uses Celery for execution; its advantage here is the application coordination built around it.

> ## Run the whole story
> There's instructions at the end of this article on how to run the whole story, and The [complete runnable project](https://github.com/gigaverse-app/due/tree/main/examples/acme_printworks) includes every model, migration, setting, provider simulator, command, and test.

## 1. Launch: count Willow Cafe's words

![Stage 1: minutes 0, 2, 3, 7 and 10; restart and late-success branches; customer outcome and Due recovery.](../documentation/assets/acme/stage-1.png?v=9b85669)

The first requirement is modest: count each uploaded revision so Acme can prepare an estimate. Revision 17 says `Opening day: June 12`, which has four words.

The model keeps each revision separate. All application write paths must treat its text as immutable:

```python
# acme/models.py
class Revision(models.Model):
    job = models.ForeignKey(Job, on_delete=models.PROTECT)
    number = models.PositiveIntegerField()
    text = models.TextField()
    word_count = models.PositiveIntegerField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["job", "number"], name="acme_revision_number")]
```

The task is ordinary Django Tasks code:

```python
# acme/tasks.py
from django.conf import settings
from django.tasks import task

from .models import Revision


@task(queue_name=settings.CELERY_TASK_DEFAULT_QUEUE)
def count_words(revision_id: int) -> int:
    revision = Revision.objects.get(pk=revision_id)
    count = len(revision.text.split())
    Revision.objects.filter(pk=revision_id).update(word_count=count)
    return count
```

If the worker stores the count and then dies before acknowledging success, another attempt writes the same count. That is why this function is safe to replay.

Commit the revision and its task together:

```python
# acme/services.py
@transaction.atomic
def add_revision(job_id, number, text):
    revision = Revision.objects.create(job_id=job_id, number=number, text=text)
    count_words.enqueue(revision.pk)
    return revision
```

With Due, enqueueing inside this transaction stores the task in the same PostgreSQL transaction. A rollback removes both. The fast worker notification happens after commit. If that notification disappears, the registered recovery pass can still find the committed task.

Imagine the application loses electricity just after the upload commits. The customer can see their revision, but the fast notification never reached a worker. After power returns, the database still describes the owed word count.

This is a concrete first-day benefit: Acme does not need to build a separate outbox just to keep this task admission consistent with the revision. It depends on both writes using the same database connection.

Select the backend and declare the function safe to repeat:

```python
# project/settings.py
INSTALLED_APPS = ["due.django", "due.django.tasks", "acme.apps.AcmeConfig"]
USE_TZ = True
CELERY_BROKER_URL = "redis://127.0.0.1:56399/0"
CELERY_TASK_DEFAULT_QUEUE = "default"
TASKS = {
    "default": {
        "BACKEND": "due.django.tasks.backend.DueBackend",
        "QUEUES": ["default"],
        "OPTIONS": {"replay_safe_tasks": ["acme.tasks.count_words"]},
    },
}
```

The complete settings include PostgreSQL credentials for the local Compose service. At this first stage, startup needs one registration:

```python
# acme/apps.py, initial version
from django.apps import AppConfig


class AcmeConfig(AppConfig):
    name = "acme"

    def ready(self):
        from due.django.runtime.registry import configure_durable_work
        from due.django.tasks.runtime import django_tasks_registration

        configure_durable_work(
            [
                django_tasks_registration(recovery_queue="default", cadence_seconds=5),
            ]
        )
```

[Django Tasks](https://docs.djangoproject.com/en/6.1/topics/tasks/) supplies the task API; the backend supplies execution and storage behavior. Due's configuration still needs PostgreSQL, Celery, a broker, and Beat. If you already operate that stack, the entry cost is particularly small. The saving demonstrated here is application code and future coordination work, not a claim that every deployment has the lowest infrastructure bill.

### Build the first contract now

The allowlist says this task is safe to replay. Make that claim executable. Acme binds three shared pytest-obligation proofs to the actual function and reads the word count from the revision. The call counter checks that both executions really entered the function:

```python
# tests/test_task_contract.py
"""Application-specific replay proofs also apply to the initial Django task."""

import pytest
from due.django.conformance.provider import ProviderProbe
from due.django.testing import hold_nudges
from pytest_obligation.profiles.harmless_replay import REPLAY_SAFETY_PROOFS, ReplaySafeEffect

from acme.models import Job, Revision
from acme.services import add_revision
from acme.tasks import count_words


def prepare():
    with hold_nudges():
        return add_revision(Job.objects.create().pk, 17, "Opening day: June 12").pk


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("proof", REPLAY_SAFETY_PROOFS, ids=lambda proof: proof.__name__)
def test_word_count_contract(proof):
    execute = ProviderProbe(count_words.func, key=lambda revision_id: revision_id)
    proof(
        ReplaySafeEffect(
            name="Acme immutable revision word count",
            prepare=prepare,
            execute=execute,
            observe=lambda revision_id: Revision.objects.get(pk=revision_id).word_count,
            execution_count_for=execute.count,
            expected=4,
        )
    )
```

```bash
python -m pytest -q tests/test_task_contract.py
```

The suite checks that execution reaches the implementation, produces the expected four-word result, and preserves it on a real second call. Transaction rollback and lost-notification recovery are tested in `tests/test_story.py`. The shared Django Tasks runtime cannot infer these application-specific promises for you.

## 2. Customers want proofs, and an old worker finishes late

![Stage 2: minutes 0, 2, 3, 7 and 10; restart and late-success branches; customer outcome and Due recovery.](../documentation/assets/acme/stage-2.png?v=9b85669)

Revision 17 says June 12 and starts its ten-minute render. Two minutes later, Willow Cafe uploads revision 18 with the corrected opening date, June 19, and a repaired font. Another worker renders that corrected file quickly and publishes it at minute three. At minute ten, the original worker finally returns with the old date.

That is the delayed-success case. In the restart branch, a power outage or server restart kills the original worker at minute seven. Revision 18 is already published, so Due keeps June 19 current and does not resurrect revision 17. If the currently requested proof is still unfinished in another run, recovery starts a fresh attempt after its lease expires. That attempt uses the captured input and reruns rendering; Due does not checkpoint a PDF renderer halfway through its work.

A task that blindly updates `job.published_key` might put June 12 back in front of the customer. Acme now needs two properties: each revision writes its own immutable output, and only the current requested revision can publish the pointer.

Add a native Due work model for that obligation:

```python
# acme/models.py
class ProofWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(Job, null=True, on_delete=models.SET_NULL)
    revision_id = models.PositiveBigIntegerField()
    text = models.TextField()

    class Meta:
        indexes, constraints = durable_work_schema("acme_proof")
```

`UniverseDurableWork`, `durable_work_schema`, and the other imports are included in `acme/models.py` in the project. The work captures its input text; a worker does not fetch whatever copy happens to be current later.

Here is the early proof capability in full:

```python
# acme/stage2.py
"""The early proof implementation, before portal handoff and temporary spooling.

The final application registers effects.publish_proof. This intermediate version
uses the same work schema and is exercised directly by test_stage2.py.
"""

from due.django.public import ReplaySafeEffect

from . import providers
from .models import Job, ProofWork
from .rendering import RENDER_POLICY


class PublishProof(ReplaySafeEffect[Job, ProofWork, str]):
    def perform_effect(self, work):
        return providers.write_proof(work.text, work.provider_idempotency_key())

    def finalize_success(self, work, proof_key):
        Job.objects.filter(pk=work.owner_id).update(published_key=proof_key)


publish_proof = PublishProof.for_owner(
    owner=Job,
    work=ProofWork,
    input_fields=("universe", "revision_id", "text"),
    policy=RENDER_POLICY,
)
```

`providers.write_proof()` stores text under an immutable key. Repeating the same key with the same text succeeds; different text under that key is rejected. Due checks the captured revision and ownership before calling `finalize_success()` inside its settlement transaction.

The producer becomes a service that records the desired proof:

```python
# acme/services.py
def request_proof(revision_id):
    revision = Revision.objects.get(pk=revision_id)
    with publish_proof.atomic(revision.job_id) as pending:
        job = pending.locked_owner
        job.current_revision = revision
        job.published_key = ""  # Approval waits for this revision's proof.
        job.save(update_fields=["current_revision", "published_key"])
        return pending.request_work(
            ProofWork(
                revision_id=revision.pk,
                text=revision.text,
                universe="acme",
            )
        )
```

Add a native application contract as soon as you add this capability. Acme binds real proof requests, independently stored proof bytes, provider failures, and superseding revisions; stage 6 shows the final contract and generated suite.

The word-count task stays exactly as it was. Proof publication gets the richer API because it has a richer requirement. There is no need to force every background function into the same abstraction.

Try the specific regression:

```bash
python -m pytest -q tests/test_story.py::test_old_proof_cannot_replace_new_proof
```

The test compresses that ten-minute window: revision 18 finishes while revision 17 is returning from its renderer. The executable provider renders text previews and injects the relevant ordering and interruptions, so running the example does not require waiting ten minutes. The test reads the published proof text and requires June 19. Checking only a task's success status would miss the actual bug.

The runnable app gives proof rendering an explicit budget: 11 minutes to the soft worker limit, 12 to the hard limit, and a 15-minute ownership lease. Both proof implementations share this policy:

```python
# acme/rendering.py
"""Give the story's ten-minute render room to finish and settle."""

from datetime import timedelta

from due.django.public import DurableWorkPolicy

RENDER_SOFT_LIMIT = 11 * 60
RENDER_HARD_LIMIT = 12 * 60
RENDER_POLICY = DurableWorkPolicy(retention=None, lease_duration=timedelta(minutes=15))
```

Register the final proof capability with the matching execution limits. This entry in `AcmeConfig.ready()` replaces its default entry; the other capabilities keep their own budgets:

```python
# acme/apps.py, inside ready()
from .rendering import RENDER_HARD_LIMIT, RENDER_SOFT_LIMIT

DurableWorkRegistration(
    name="acme_proof",
    capability=publish_proof,
    queue=settings.CELERY_TASK_DEFAULT_QUEUE,
    cadence_seconds=5,
    execution_soft_time_limit=RENDER_SOFT_LIMIT,
    execution_time_limit=RENDER_HARD_LIMIT,
)
```

For a claim made at minute 0 with no renewal, that lease expires at minute 15. A minute-7 crash therefore does not cause an immediate replacement render. Once recovery is running and the work is eligible, a later tick can dispatch a new attempt. With r18 already settled at minute 3, there is no current proof left to render. Independent cleanup can still be owed. The five-second cadence is a polling interval, not a completion deadline. Set a real renderer's bounded call timeout below the soft limit; the text simulator has no ten-minute delay. See [execution budgets](https://due.readthedocs.io/en/latest/package/docs/operations/).

## 3. The approval portal can be overwritten remotely

![Stage 3: minutes 0, 2, 3, 7 and 10; restart and late-success branches; customer outcome and Due recovery.](../documentation/assets/acme/stage-3.png?v=9b85669)

Acme adds a hosted approval portal. Its API accepts `PUT(job_id, proof_key)`, but provides no conditional version check. For this stage, suppose an earlier proof, r16, already exists with June 12. Its portal request is held in flight before our clock starts. While r17 is rendering, the minute-2 correction produces r18 and its portal update at minute 3. Restarting Acme at minute 7 cannot retract the older HTTP request. If that request lands at minute 10, the portal shows June 12 again, even though the killed r17 renderer never returned.

The database fence from stage 2 protects Acme's local pointer. The portal needs repair after an escaped old write. Model that requirement with a `Projection`:

```python
# acme/models.py
class PortalWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(Job, null=True, on_delete=models.SET_NULL)
    proof_key = models.CharField(max_length=200)

    class Meta:
        indexes, constraints = durable_work_schema("acme_portal")
```

```python
# acme/effects.py
class PortalProjection(Projection[Job, PortalWork]):
    def write_projection(self, work):
        providers.put_portal(work.owner_id, work.proof_key)

    def on_projected(self, work):
        Job.objects.filter(pk=work.owner_id).update(portal_failed=False)

    def finalize_retry(self, work, error):
        if work.state == DurableWorkState.EXHAUSTED:
            Job.objects.filter(pk=work.owner_id).update(portal_failed=True)


project_portal = PortalProjection.for_owner(
    owner=Job,
    work=PortalWork,
    input_fields=("universe", "proof_key"),
    policy=DurableWorkPolicy(retention=None, repair_interval=timedelta(seconds=5)),
)
```

When proof publication settles, also admit the desired portal update. Replace the proof capability's `finalize_success()` with this method:

```python
# Method on PublishProof, in acme/effects.py
def finalize_success(self, work, proof_key):
    Job.objects.filter(pk=work.owner_id).update(published_key=proof_key)
    with project_portal.atomic(work.owner_id) as pending:
        pending.request_work(PortalWork(proof_key=proof_key, universe="acme"))
```

The complete class appears in stage 5 below. The pointer and portal obligation commit together. A lost follow-up notification leaves a recoverable obligation.

Due's projection machinery retains evidence about older calls and tracks repair that is still owed. After a late write, recovery reapplies the current desired proof. The portal can briefly show the old value; the promise here is eventual repair, with the necessary history retained.

Run the test that actually puts the wrong proof back on the portal:

```bash
python -m pytest -q tests/test_story.py::test_late_portal_write_is_repaired
```

It first verifies that June 12 overwrote June 19, then runs recovery and requires the portal to show June 19 again. Acme has gained remote repair without writing its own stale-attempt ledger and reconciliation scheduler.

## 4. A timeout after ordering 500 brochures

![Stage 4: minutes 0, 2, 3, 7 and 10; restart and late-success branches; customer outcome and Due recovery.](../documentation/assets/acme/stage-4.png?v=9b85669)

Willow Cafe approves revision 18 after it publishes at minute 3. Acme submits 500 copies to a printing partner just before minute 7. The printer creates the order and commits it. Then the minute-7 restart kills Acme's waiting client before it records the reply. A power outage at the printer's network gateway would leave Acme with the same uncertainty. At minute 10, the question is whether the order was accepted, regardless of whether the obsolete r17 render survived.

This provider creates a new order on **every POST**. Its reference can be searched later, but it is not an idempotency key. Blindly repeating the request might buy another 500 brochures.

The simulator makes that failure real:

```python
# acme/providers.py
@dataclass(frozen=True)
class PrintReceipt:
    order_id: int
    reference: str
    proof_key: str
    quantity: int


def submit_print(reference, proof_key, quantity):
    with database() as db:
        cursor = db.execute(
            "INSERT INTO orders(reference,proof_key,quantity) VALUES (?,?,?)",
            (reference, proof_key, quantity),
        )
        assert cursor.lastrowid is not None
        receipt = PrintReceipt(cursor.lastrowid, reference, proof_key, quantity)
        lost = db.execute("DELETE FROM faults WHERE name='print_reply'").rowcount
    # The provider has COMMITTED. Rolling back Django cannot undo this order.
    if lost:
        raise TimeoutError("Printer accepted the order; response was lost")
    return receipt
```

The timeout happens after an independent provider commit. A rollback in Acme's PostgreSQL transaction cannot undo that order.

Store the approved purchase intent separately from the mutable job:

```python
# acme/models.py
class PrintOrder(models.Model):
    reference = models.UUIDField(default=uuid4, unique=True, editable=False)
    revision = models.ForeignKey(Revision, on_delete=models.PROTECT)
    proof_key = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=500)
    status = models.CharField(max_length=20, default="pending")
    provider_order_id = models.PositiveBigIntegerField(null=True)


class PrintWork(ObservedExternalOperationWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(PrintOrder, null=True, on_delete=models.SET_NULL)
    reference = models.UUIDField()
    proof_key = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField()

    class Meta:
        indexes, constraints = durable_work_schema("acme_print")
        indexes.append(observation_index("acme_print_observe"))


class PrintEvidence(StoredAppliedEffectEvidence):
    pass
```

Use an `ObservedExternalOperation` to distinguish uncertainty from permission to retry:

```python
# acme/effects.py
mirror = StatusMirror(lambda work: PrintOrder.objects.filter(pk=work.owner_id))


class OrderPrinting(ObservedExternalOperation[PrintOrder, PrintWork, providers.PrintReceipt]):
    observation_interval = timedelta(seconds=5)
    evidence_store = JsonAppliedEffectEvidenceStore(PrintEvidence, providers.PrintReceipt)
    on_applied = mirror.outcome(
        lambda work, receipt: {
            "status": "accepted",
            "provider_order_id": receipt.order_id,
        }
    )
    on_unknown = mirror.outcome({"status": "unknown"})
    on_retry = mirror.outcome({"status": "retrying"})
    on_exhausted = mirror.outcome({"status": "stopped"})
    on_terminal = mirror.outcome({"status": "stopped"})

    def perform_effect(self, work):
        try:
            return providers.submit_print(str(work.reference), work.proof_key, work.quantity)
        except providers.PrinterRejected as error:
            # Only a definite refusal before acceptance permits retrying a purchase.
            raise EffectNotAppliedError(str(error)) from error

    def observe_effect(self, work):
        receipt = providers.observe_print(str(work.reference))
        if receipt is not None and (
            receipt.reference != str(work.reference)
            or receipt.proof_key != work.proof_key
            or receipt.quantity != work.quantity
        ):
            raise ValueError("Receipt does not match the captured print request")
        return receipt


order_printing = OrderPrinting.for_owner(
    owner=PrintOrder,
    work=PrintWork,
    input_fields=("universe", "reference", "proof_key", "quantity"),
    policy=POLICY,
)
```

`POLICY` is `DurableWorkPolicy(retention=None)` in this sample. The capability retains the applied receipt in `PrintEvidence`. Observation checks the reference, exact proof key, and quantity before treating the receipt as evidence for this request.

The approval service locks the job and rejects a stale proof approval:

```python
# acme/services.py
@transaction.atomic
def approve_print(job_id, expected_proof_key, quantity=500):
    job = Job.objects.select_for_update().get(pk=job_id)
    if not job.published_key or job.published_key != expected_proof_key:
        raise ValueError("Proof changed; review the current proof before approving")
    order = PrintOrder.objects.create(
        revision=job.current_revision,
        proof_key=job.published_key,
        quantity=quantity,
    )
    with order_printing.atomic(order.pk) as pending:
        pending.request_work(
            PrintWork(
                reference=order.reference,
                proof_key=order.proof_key,
                quantity=order.quantity,
                universe="acme",
            )
        )
    return order
```

Once admitted, this print request captures the approved proof. Later edits to the job do not change the purchase that was approved. Each call creates a distinct purchase intent; an HTTP endpoint should also deduplicate repeated browser submissions.

After the simulated timeout, the order becomes `unknown`. Recovery searches for a correlated receipt and records `accepted`, while the provider's order count stays one. A missing receipt alone does not authorize another POST. Also, “accepted” says nothing yet about printing or shipping.

Here is the application test, including its independent provider-side count:

```python
# tests/test_story.py
def test_accepted_print_with_lost_reply_is_observed_without_resubmit():
    job = published()
    providers.lose_next_print_reply()
    with hold_nudges():
        order = approve_print(job.pk, job.published_key)
        with pytest.raises(EffectOutcomeUnknownError):
            order_printing.attempt_execution(order.printwork.pk)
    order.refresh_from_db()
    assert order.status == "unknown"
    assert providers.print_count(str(order.reference)) == 1
    with WorkTableDelivery(order_printing).session() as delivery:
        delivery.recover()
    order.refresh_from_db()
    assert order.status == "accepted"
    assert providers.print_count(str(order.reference)) == 1
    receipt = providers.observe_print(str(order.reference))
    assert receipt is not None
    assert receipt.order_id == order.provider_order_id
```

Now consider a different incident: the printer is out of ink and explicitly rejects the request **before accepting an order**. The adapter raises `PrinterRejected`; `OrderPrinting` translates only that definite refusal into `EffectNotAppliedError`. Due can retry under its policy after supplies return. The contract exercises both histories:

```python
# tests/contracts/printer.py
rejected = SEAM.rejection(
    lambda provider, work: provider.submit.reject(lambda: providers.PrinterRejected("Out of ink; order NOT accepted")),
    lambda provider, work: "retrying",
    WorkFailureDisposition.RETRYABLE,
)


@contextmanager
def lost_response(provider):
    with (
        provider.submit.lose_response(lambda: TimeoutError("Printer UPS failed after acceptance; reply lost")),
        patch.object(providers, "receipts_visible", False),
    ):
        yield


uncertain = SEAM.outcome(lambda provider, work: lost_response(provider), lambda provider, work: "unknown")


@contextmanager
def observation():
    with uncertain() as scenario:

        def reveal():
            providers.receipts_visible = True
            return providers.print_receipt(str(scenario.work.reference))

        yield ObservationExample(
            delivery=replace(scenario, expected="accepted"),
            reveal=reveal,
            observation_calls=lambda: calls.get().observe.count(str(scenario.work.reference)),
        )
```

An ink shortage after acceptance is a fulfillment delay. Acme must follow the existing order's production status; submitting another order would compound the problem. Due's acceptance receipt does not mean paper was printed or shipped.

That is a strong reason to choose Due when your Django app makes consequential external requests: uncertainty becomes a state you can inspect and resolve with evidence, using a different policy from an ordinary replayable task.

## 5. Rendering gets temporary storage, and workers can die

![Stage 5: minutes 0, 2, 3, 7 and 10; restart and late-success branches; customer outcome and Due recovery.](../documentation/assets/acme/stage-5.png?v=9b85669)

Acme's renderer now creates temporary spool data during those ten minutes of preparing a proof. Suppose a power outage or unexpected server restart stops it at minute seven, after it has written a multi-gigabyte spool file. Deleting that file only in a Python `finally` block is insufficient: the worker is gone before the block can run. A week of stranded files can leave too little space for the next customer. The corrected r18 proof published at minute 3 remains valid; cleaning r17's temporary files must not delete it. After the old attempt's reservation expires, Due can recover its independent cleanup. It removes disposable bytes and keeps checking until the renderer provides completion evidence that rules out later writes.

Reserve a cleanup obligation **before** calling the renderer:

```python
# acme/models.py
class CleanupWork(UniverseDurableWork):
    spool_key = models.CharField(max_length=200)

    class Meta:
        indexes, constraints = durable_work_schema("acme_cleanup")
```

```python
# acme/effects.py
class SpoolCleanup(IndependentCompensation[ProofWork, CleanupWork]):
    def capture(self, producer):
        return CleanupWork(spool_key=str(producer.token), universe="acme")

    def compensate(self, work):
        return providers.cleanup(work.spool_key)


cleanup_spool = SpoolCleanup(
    producer_model=ProofWork,
    model=CleanupWork,
    nudge=registered_nudge(CleanupWork),
    policy=DurableWorkPolicy(retention=None, max_rearms=None),
    inspection_interval=timedelta(seconds=5),
)
```

The final `acme/effects.py` imports `RENDER_POLICY` from `.rendering`, just as stage 2 does. The cleanup identity uses the producer's attempt token, so separate attempts do not share temporary storage. The renderer keeps a terminal completion record. After that record exists, the attempt cannot write more spool data.

Cleanup checks that evidence before reporting completion:

```python
# acme/providers.py
def cleanup(spool_key):
    with database() as db:
        row = db.execute("SELECT finished FROM renders WHERE key=?", (spool_key,)).fetchone()
        if row is None:
            return False  # Absence is not proof that a delayed write cannot arrive.
        db.execute("UPDATE renders SET spool=NULL WHERE key=?", (spool_key,))
    return bool(row[0])
```

An absent object does not prove that an earlier write cannot arrive later. Here, a missing completion record keeps cleanup owed. A real renderer needs an equally explicit completion or cancellation contract.

Now replace the early `ReplaySafeEffect` subclass with `OwnedDurableWorkCapability`, whose `apply()` receives the execution context needed to reserve cleanup. This is the final proof capability:

```python
# acme/effects.py
def render_proof(work):
    return providers.render(work.text, work.provider_idempotency_key(), str(work.fencing_token))


class PublishProof(OwnedDurableWorkCapability[Job, ProofWork, str]):
    def apply(self, work, context):
        with cleanup_spool.record_cleanup_before_call(context) as output:
            key = render_proof(work)
            if providers.cleanup(str(work.fencing_token)):
                output.confirm_compensated()
            return key

    def finalize_success(self, work, proof_key):
        Job.objects.filter(pk=work.owner_id).update(published_key=proof_key, proof_failed=False)
        # Both the pointer and the portal obligation commit in this settlement.
        with project_portal.atomic(work.owner_id) as pending:
            pending.request_work(PortalWork(proof_key=proof_key, universe="acme"))

    def finalize_retry(self, work, error):
        if work.state == DurableWorkState.EXHAUSTED:
            Job.objects.filter(pk=work.owner_id).update(proof_failed=True)


publish_proof = PublishProof.for_owner(
    owner=Job,
    work=ProofWork,
    input_fields=("universe", "revision_id", "text"),
    policy=RENDER_POLICY,
)
```

The ordinary path renders, removes the temporary spool, and acknowledges compensation. If the process dies after rendering, the separately committed cleanup obligation remains. Recovery can clean it after its reservation expires. The immutable proof itself is retained.

The same class also keeps stage 3's transactional portal handoff. This is how the example grows: the publication input and settlement remain recognizable, while the provider boundary gains the specific protection it now needs.

Register all the capabilities alongside the original Django task backend:

```python
# acme/apps.py
from django.apps import AppConfig
from django.conf import settings


class AcmeConfig(AppConfig):
    name = "acme"

    def ready(self):
        from due.django.runtime.registry import (
            DurableWorkRegistration,
            configure_durable_work,
        )
        from due.django.tasks.runtime import django_tasks_registration

        from .effects import (
            cleanup_spool,
            order_printing,
            project_portal,
            publish_proof,
        )
        from .rendering import RENDER_HARD_LIMIT, RENDER_SOFT_LIMIT

        configure_durable_work(
            [
                django_tasks_registration(recovery_queue=settings.CELERY_TASK_DEFAULT_QUEUE, cadence_seconds=5),
                DurableWorkRegistration(
                    name="acme_proof",
                    capability=publish_proof,
                    queue=settings.CELERY_TASK_DEFAULT_QUEUE,
                    cadence_seconds=5,
                    execution_soft_time_limit=RENDER_SOFT_LIMIT,
                    execution_time_limit=RENDER_HARD_LIMIT,
                ),
                *[
                    DurableWorkRegistration(
                        name=name,
                        capability=capability,
                        queue=settings.CELERY_TASK_DEFAULT_QUEUE,
                        cadence_seconds=5,
                    )
                    for name, capability in [
                        ("acme_portal", project_portal),
                        ("acme_print", order_printing),
                        ("acme_cleanup", cleanup_spool),
                    ]
                ],
            ]
        )
```

Each registration gives recovery an owner, queue, and cadence. The project's Celery setup uses the same Django settings and database. `run_demo.py` starts both the worker and Beat, including these recovery schedules.

## 6. Keep the failure guarantees executable

![Stage 6: minutes 0, 2, 3, 7 and 10; restart and late-success branches; customer outcome and Due recovery.](../documentation/assets/acme/stage-6.png?v=9b85669)

Acme changes its printer adapter six months later. A developer sees a timeout and adds a retry. The happy-path demo still works, but the next power failure could order a second 500 brochures. A contract should catch that before deployment.

**Always build the application contract.** Due and pytest-obligation provide reusable failure histories and assertions. You supply the application meaning: how work is admitted, which provider call matters, what a refusal means, and what the customer should actually see. You get valuable runnable tests without designing every lease, ownership, recovery and replay interleaving yourself.

Start by configuring the test host:

```python
# tests/conftest.py
import pytest
from due.django.conformance.host import work_table_host
from pytest_obligation import configure

configure(work_table_host(production_packages={"acme"}))


@pytest.fixture(autouse=True)
def isolated_provider(settings, tmp_path):
    settings.PROVIDER_DB = tmp_path / "provider.sqlite3"
```

For proof publication, the seam observes the published bytes and independently stored provider bytes. `ProviderProbe` wraps the real local provider transaction; its refusal control simulates an unavailable renderer while preserving real Due execution and PostgreSQL settlement:

```python
# tests/contracts/proof.py
SEAM = ProviderSeam(
    environment=environment,
    make_work=make_proof,
    observe=lambda provider, work: proof_contents(work),
    delivered=lambda provider, work: COPY,
    provider_state=lambda provider, work: provider_contents(work),
    provider_expected=lambda provider, work: COPY,
    calls=lambda provider: provider.render.total,
    calls_for=lambda provider, work: provider.render.count(work.provider_idempotency_key()),
    refuse=lambda provider: provider.render.reject(lambda: OSError("Renderer power supply unavailable")),
)
```

The helpers live in `tests/contracts/support.py`. They call `add_revision` and `request_proof`, count actual provider I/O, and read the provider's separate SQLite database. The remaining bindings connect terminal outcomes, supersession, admission rollback, and cleanup composition:

```python
# tests/contracts/proof.py
EXAMPLES = seam_examples(
    publish_proof,
    SEAM,
    replay=SEAM.replay("Acme immutable rendering", render_proof),
    environment=environment,
    terminal=lambda: TerminalExample(
        observe=lambda work: Job.objects.get(pk=work.owner_id).proof_failed,
        rearm=lambda work: retry_proof(work.owner_id),
    ),
    snapshot=snapshot,
    compensation=compensation,
    producers={"request proof": producer},
    admission_failures={"partial proof request": admission_failure},
    convergence_families={
        family: NotApplicable(
            "Immutable proof keys keep old bytes separate; the current revision's fenced pointer is the only mutable result. There is no merge or dependency gate."
        )
        for family in ConvergenceFamily
        if family.name != "STALE_SNAPSHOTS"
    },
)
```

For example, supersession changes the desired proof through the production service:

```python
# tests/contracts/proof.py
def snapshot():
    work = make_proof()
    return SupersessionExample(
        work=work,
        change=lambda: request_proof(add_revision(work.owner_id, 19, NEW_COPY).pk),
        observe=lambda: proof_contents(work),
    )
```

The complete `proof.py` contains the producer and compensation helpers referenced above. `portal.py`, `printer.py`, and `cleanup.py` bind the other capabilities. The cleanup lifecycle fixture begins with an owed row and real disposable bytes; the producer's composition histories and the subprocess test separately prove that cleanup was admitted before rendering. The bindings do not implement Due's recovery or settlement logic.

These four declarations generate tests from the registered production capabilities and their actual policies:

```python
# tests/test_contracts.py
"""These classes collect real tests, selected by each capability's policies."""

from due.django.conformance.suite import effect_contract_suite

from acme.effects import cleanup_spool, order_printing, project_portal, publish_proof

from .contracts import cleanup, portal, printer, proof


@effect_contract_suite(publish_proof, examples=proof.EXAMPLES)
class TestProofContract:
    pass


@effect_contract_suite(project_portal, examples=portal.EXAMPLES)
class TestPortalContract:
    pass


@effect_contract_suite(order_printing, examples=printer.EXAMPLES)
class TestPrinterContract:
    pass


@effect_contract_suite(cleanup_spool, examples=cleanup.EXAMPLES)
class TestCleanupContract:
    pass
```

Run them and keep the evidence:

```bash
python -m pytest -q -p due.django.conformance.reporting \
  --due-work-require-assessed \
  --due-work-profile-report=profiles.json \
  --durable-work-report=effects.json
```

The current example runs **286 tests**. Of those, **270 are generated native contract tests** and three are the first task's shared replay proofs. Ten scenario tests cover the application's specific story, including a real subprocess exit; one budget check verifies that the registered worker allows the ten-minute render; two enrollment guards check that native contracts cannot disappear unnoticed.

| Contract | Generated checks | Customer promise exercised |
| --- | ---: | --- |
| Proof publication | 66 | Current proof wins; failed rendering remains recoverable; cleanup survives the producer |
| Approval portal | 70 | Late remote writes are repaired to the current proof |
| Print ordering | 80 | Definite refusal can retry; ambiguous acceptance requires observation; receipts repair local state |
| Temporary spool cleanup | 54 | Cleanup survives loss of ownership and retries until it has completion evidence |

The actual policies determine the applicable proofs. Acme also supplies explicit reasons for inapplicable families. A green contract says these application bindings passed their declared histories; it cannot establish undocumented behavior of a real printing vendor.

Keep an inventory independent of the test declarations:

```python
# tests/test_enrollment.py
"""A missing or disabled contract must not turn into a smaller green suite."""

from pathlib import Path

import pytest
from due.django.conformance.enrollment import assert_complete_enrollment, discover_enrollment
from due.django.runtime.registry import installed
from due.django.tasks.runtime import TaskCapability


def native_capabilities():
    # Inventory comes from production registration, independently of test classes.
    # Django Tasks has a shared runtime capability; its application task bodies
    # need their own replay/transaction tests (see test_story.py).
    return {
        entry.name: entry.capability
        for entry in installed.get().values()
        if not isinstance(entry.capability, TaskCapability)
    }


def test_every_native_capability_has_a_contract():
    assert_complete_enrollment(native_capabilities(), discover_enrollment(Path(__file__).parent))


def test_deleted_contract_is_rejected():
    enrolled = discover_enrollment(Path(__file__).parent)
    assert len(enrolled) == 4
    for omitted in enrolled:
        with pytest.raises(AssertionError, match="conformance enrollment"):
            assert_complete_enrollment(native_capabilities(), [entry for entry in enrolled if entry != omitted])
```

CI additionally uses Due's `scripts/check_adoption_report.py` to require every expected proof to be collected, selected, executed and passed for all four named capabilities. `--due-work-require-assessed` catches missing bindings; the inventory and report gates catch missing suites or filtered execution. Do not replace those checks with a test-count assertion.

For the physical process-death scenario:

```bash
python -m pytest -q tests/test_story.py::test_independent_cleanup_survives_process_death
```

That test compresses a process-death history: it runs a separate Python process, commits the renderer output and calls `os._exit(79)` before publication. It covers death after provider completion; the generated compensation histories also cover cleanup while completion is unresolved. Together with the supersession test, these exercise the distinct boundaries in our minute-7 story, without claiming a real ten-minute PDF run. Its parent verifies the crash point, recovers cleanup, checks the spool bytes disappeared, and recovers publication. `run_demo.py` separately exercises Redis, Celery workers and Beat, so the contract suite and the live journey cover different parts of the integration.

When Acme adds a provider, update its contract in the same change. A new lookup, idempotency or completion promise should have a binding and an observable verdict. This is how the initial small task grows into a system whose reliability claims remain reviewable and runnable.

## When should you choose Due over the alternatives?

**Choose Due when you expect background work to become part of your product's correctness.** Acme's roadmap is a good example: edits can overtake each other, remote writes can arrive late, and a repeated request can place a second physical order. Due gives those requirements explicit models and reusable recovery behavior.

| Acme's requirement | What Due lets the application reuse |
| --- | --- |
| Commit a revision and its task together | PostgreSQL task admission and scheduled recovery of lost notifications |
| Publish the latest requested proof | Captured inputs, revision ownership, and fenced local settlement |
| Repair an unconditional remote portal | Retained attempt evidence and desired-state projection repair |
| Avoid repeating an uncertain purchase | An unknown state, correlated receipts, and observation before further action |
| Finish after a process dies | Durable follow-up admission and independent cleanup obligations |
| Preserve those guarantees during changes | Application contracts generate policy-specific failure tests; enrollment and report gates prevent silent coverage loss |

If your app only needs a few disposable background jobs, a simpler worker may be enough. Celery also supplies broad task execution features and is Due's worker engine here. The reason to add Due is that you would otherwise have to build and maintain the coordination in this table yourself, or integrate another system that provides it.

For Acme, that investment starts small: keep the estimator's `@task`, configure one backend, and commit the task with the revision. As requirements grow, add the native capability that matches the problem. There is no seperate migration of every task, and no need to anticipate every future workflow on day one.


[Get the complete Acme Printworks project](https://github.com/gigaverse-app/due/tree/main/examples/acme_printworks) · [Due capabilities and contracts](https://github.com/gigaverse-app/due/blob/v0.8.0/package/docs/capabilities.md)

## Run the whole story
The [complete runnable project](https://github.com/gigaverse-app/due/tree/main/examples/acme_printworks) includes every model, migration, setting, provider simulator, command, and test. The snippets above explain its evolution. The downloadable application contains the final version; `acme/stage2.py` also preserves and tests the intermediate proof implementation.

Use Python 3.14 and Docker Compose on Linux, macOS, or WSL:

```bash
git clone https://github.com/gigaverse-app/due.git
cd due/examples/acme_printworks
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
docker compose up -d --wait
python run_demo.py
python -m pytest -q -p due.django.conformance.reporting --due-work-require-assessed
```

The demo applies migrations, starts a Celery worker and Beat, runs the order, then stops those processes. The first run prints:

```text
Acme Printworks job 418: Willow Cafe, 500 brochures
1. Estimate: 4 words, recovered after a lost notification
2. Proof: revision 18, June 19
3. Portal: displays revision 18
4. Printer: accepted 500 copies; lost reply recovered; exactly one order
5. Cleanup: temporary spool removed; immutable proof retained
6. Run pytest -q for late writes, process death, and replay contracts
```

Subsequent runs create a new job. Run `docker compose down` when finished. These are disposable local development services. The README also explains how to use existing dedicated PostgreSQL and Redis instances.

> The providers are local simulators with independent SQLite commits. Django and Due use PostgreSQL. The proof is a text preview, keeping the example small; no PDF library, printer account, or API key is needed. We deliberately give the portal and printer difficult contracts so the failure cases are reproducible.
