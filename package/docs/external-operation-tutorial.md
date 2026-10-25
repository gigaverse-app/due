# 5. Preserve an uncertain outcome

[Previous: scheduling](scheduling-tutorial.md) · [All chapters](tutorial.md)

You submit an order. The provider accepts it, but the response never reaches your
worker: the connection drops, or your timeout fires a moment too early. Did the
order happen? You cannot tell. This is an
[unknown outcome](vocabulary.md#unknown-outcome), and it is how customers end up
charged twice: the app treats "no reply" as "failed" and submits again.

Everything in chapters 2–4 was [replay-safe](vocabulary.md#replay-and-replay-safe),
so Due could simply run it again. An order is not. If the provider honours an
[idempotency key](vocabulary.md#idempotency-key), send one and the problem
mostly disappears. When it does not, use `DurableExternalOperation`. Before
calling the provider, it records that an attempt is about to cross the boundary.
If the reply is lost, the work stays owed with its outcome marked *unknown*, and
Due never resubmits it on its own. It waits for
[evidence](vocabulary.md#evidence-and-receipt) of what actually happened.

This chapter uses a local SQLite table as the provider. Each submission inserts
one row. There is no printer account or network service to set up. Use the
[prepared native shell](tutorial.md#prepare-the-native-chapters-once).

## 1. Define the operation's result

A receipt identifies what the provider accepted:

```python
from pydantic import BaseModel


class PrintReceipt(BaseModel):
    order_id: int
    reference: str
    title: str
```

The prepared [provider.py](../../examples/orders/provider.py) defines this type
and these small operations:

| Function | What it does |
| --- | --- |
| `submit(reference, title)` | Inserts a new order and returns its receipt; every call creates a row |
| `observe(reference)` | Reads an existing receipt, or returns `None` while receipts are hidden |
| `receipt(reference)` | Inspects the provider's ledger directly, for our assertions |

`reference` correlates a receipt with a request. This provider does **not** use it
to deduplicate submissions. The exercise can therefore detect an accidental resend.

## 2. Store work and evidence separately

The product is `PrintOrder`, a plain Django model. `status` is a label for the
user, separate from Due's own work state:

```python
from uuid import uuid4

from django.db import models


class PrintOrder(models.Model):
    reference = models.UUIDField(default=uuid4, unique=True, editable=False)
    title = models.CharField(max_length=100)
    provider_order_id = models.PositiveBigIntegerField(null=True)
    status = models.CharField(max_length=20, default="pending")
```

This capability also needs two Due base models:

- `ExternalOperationWork` is the work-model base for operations that must not
  repeat. It plays the role `UniverseDurableWork` played in chapter 2 and adds
  no columns of its own. Instead it marks the work as needing evidence: Due
  records each provider attempt in a table of its own, and while an attempt's
  outcome is unresolved, Due will not submit again.
- `StoredAppliedEffectEvidence` is the base for a small table where Due stores
  each receipt it obtains. You subclass it once; you never write to it yourself.

```python
from due.django.public import StoredAppliedEffectEvidence


# Due base for the table that stores receipts; Due writes to it, your code never does.
class PrintEvidence(StoredAppliedEffectEvidence):
    pass
```

The prepared [models.py](../../examples/orders/models.py) already includes
`PrintOrder`, `PrintWork` and `PrintEvidence`. `PrintWork` captures the order's
reference and title. It uses `ObservedExternalOperationWork`, an extension of
`ExternalOperationWork`, so chapter 6 can add recovery by reading the receipt;
[chapter 6 shows its definition](observation-tutorial.md#1-add-observation-state-to-the-work-model).

## 3. Define submission and outcome hooks

Here is the submission-only form of the capability. Read this definition; the
prepared app already registers the observed version shown in chapter 6.

Instead of one `finalize_success()`, this capability has a settlement hook for
each way an attempt can end, because "unknown" and "retrying" are outcomes the
user should see too. `StatusMirror` is a Due helper that keeps those hooks to
one line each: you tell it which product rows to update, and each `mirror.outcome(...)`
declares the fields to set.

```python
from due.django.public import (
    DurableExternalOperation,
    EffectNotAppliedError,
    JsonAppliedEffectEvidenceStore,
    StatusMirror,
)
from examples.orders import provider
from examples.orders.models import PrintEvidence, PrintOrder, PrintWork
from examples.orders.provider import PrintReceipt

# Names the product rows the outcome hooks below update: this work's order.
mirror = StatusMirror(lambda work: PrintOrder.objects.filter(pk=work.owner_id))


# Due base for an operation that must not be repeated blindly; PrintReceipt is its result type.
class SubmitOrder(DurableExternalOperation[PrintOrder, PrintWork, PrintReceipt]):
    # Saves each receipt as JSON, so recovery can settle without calling the provider again.
    evidence_store = JsonAppliedEffectEvidenceStore(PrintEvidence, PrintReceipt)
    # One settlement hook per ending: applied, unknown, retrying, out of retries, stopped for good.
    on_applied = mirror.outcome(lambda work, receipt: {"provider_order_id": receipt.order_id, "status": "printed"})
    on_unknown = mirror.outcome({"status": "unknown"})
    on_retry = mirror.outcome({"status": "retrying"})
    on_exhausted = mirror.outcome({"status": "stopped"})
    on_terminal = mirror.outcome({"status": "stopped"})

    def perform_effect(self, work: PrintWork) -> PrintReceipt:
        try:
            # Before this call, Due recorded the attempt as possibly applied.
            return provider.submit(str(work.reference), work.title)
        except provider.PrinterRejected as error:
            # Only a definite rejection permits a retry. Any other error leaves the outcome unknown.
            raise EffectNotAppliedError(str(error)) from error
```

| Code | Comes from | What it does |
| --- | --- | --- |
| `DurableExternalOperation[PrintOrder, PrintWork, PrintReceipt]` | Due | The capability base for an operation that must not be repeated blindly. Type parameters: the owner model, the work model, and the receipt type `perform_effect()` returns. |
| `perform_effect(self, work)` | A hook you implement | Calls the provider once and returns its receipt. Due records the attempt as possibly applied before calling it. |
| `evidence_store = JsonAppliedEffectEvidenceStore(PrintEvidence, PrintReceipt)` | Due | Where receipts are kept: it saves each `PrintReceipt` as JSON in the `PrintEvidence` table. Once a receipt is saved, recovery can finish settlement from it without calling the provider again. |
| `StatusMirror(lambda work: ...)` | Due | Names the product rows the outcome hooks update: here, the order this work belongs to. |
| `on_applied` | Settlement hook | The provider definitely did it; receives the receipt. |
| `on_unknown` | Settlement hook | The call may or may not have happened. The work stays owed and will not be resubmitted. |
| `on_retry` | Settlement hook | The provider definitely did *not* do it, and another attempt is allowed. |
| `on_exhausted` | Settlement hook | Like `on_retry`, but the [retry budget](vocabulary.md#retry-budget) has run out. |
| `on_terminal` | Settlement hook | The work stopped for good on a failure that will not be retried. |
| `EffectNotAppliedError` | Due | Raise it to tell Due the provider *definitely* rejected the request, so retrying cannot create a duplicate. |
| `provider.submit()`, `provider.PrinterRejected` | This example | The simulated printer and its definite rejection. |

The most important line is the `except` clause. Only the provider's definite
rejection becomes `EffectNotAppliedError`, which permits a retry. Any other
exception, including a timeout, leaves the outcome unknown. Do not translate
every provider exception into permission to try again.

The sample's `printed` status means the provider accepted the order; it does not
claim physical printing has finished.

## 4. Submit once and lose the reply

Now use the prepared, registered instance. Two environment variables switch on
this example's simulated faults (they belong to the simulator, not to Due): `DUE_PRINTER_LOSE_RESPONSE` makes the printer
accept the order and then raise `TimeoutError` instead of replying, and
`DUE_PRINTER_HIDE_RECEIPTS` makes its receipts unreadable for now. The
registered instance also has the observation hook from chapter 6, which the
second switch disables, so it cannot resolve the uncertainty yet.
`EffectOutcomeUnknownError` is what `attempt_execution()` raises when an attempt
ends with an unknown outcome:

```python
import os

from due.django.conformance import WorkTableDelivery, hold_nudges
from due.django.public import EffectOutcomeUnknownError
from examples.orders import provider
from examples.orders.effects import print_order
from examples.orders.models import PrintOrder
from examples.orders.services import order_print

os.environ["DUE_PRINTER_LOSE_RESPONSE"] = "1"  # Example fault: accept the order, then time out.
os.environ["DUE_PRINTER_HIDE_RECEIPTS"] = "1"  # Example fault: no receipt can be read yet.

with hold_nudges():
    work = order_print("Hello from Due")
    try:
        print_order.attempt_execution(work.pk)
    except EffectOutcomeUnknownError:  # The attempt ended without a definite answer.
        print("The provider may have accepted the order")

order = PrintOrder.objects.get(pk=work.owner_id)
accepted = provider.receipt(str(work.reference))  # Peek at the provider's ledger: the order exists.
assert accepted is not None
print(order.status, order.provider_order_id)  # unknown None
print(accepted.title)  # Hello from Due
```

`order_print()` creates the product and calls `pending.request_work()` in one
transaction, just as chapter 2 did. The provider commits its separate SQLite
transaction, then deliberately raises `TimeoutError`. The assertion confirms
that acceptance really happened while the application still lacks its receipt.

## 5. Recover while the answer is still unavailable

```python
# One recovery round: the outcome stays unknown, and nothing is submitted again.
with WorkTableDelivery(print_order, rounds=1).session() as delivery:
    delivery.recover()

order.refresh_from_db()
print(order.status)  # unknown
with provider.connection() as connection:
    count = connection.execute(
        "SELECT count(*) FROM print_orders WHERE reference = ?", (str(work.reference),)
    ).fetchone()[0]
print(count)  # 1
```

This performs one recovery round with the receipt still hidden. Recovery
preserves the unknown outcome. A missing receipt does not prove that
submission failed. Keep this shell open for chapter 6, where you make the receipt
visible and settle the same order without submitting again.

If you stop here, clear the fault switches before making unrelated requests:

```python
os.environ.pop("DUE_PRINTER_LOSE_RESPONSE", None)
os.environ.pop("DUE_PRINTER_HIDE_RECEIPTS", None)
```

[Next: resolve uncertainty by reading](observation-tutorial.md)
