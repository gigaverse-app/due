# 5. Preserve an uncertain outcome

[Previous: scheduling](scheduling-tutorial.md) · [All chapters](tutorial.md)

You submit an order. The provider accepts it, but the response never reaches your
worker. Submitting again might create a second order. `DurableExternalOperation`
records that uncertainty and keeps the operation owed without automatically
repeating an action that might already have happened.

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

For a submission-only capability, derive your captured work model from
`ExternalOperationWork`. It adds the state needed to track a possibly escaped
provider call. Store successful receipts in a concrete evidence model:

```python
from due.django.public import StoredAppliedEffectEvidence


class PrintEvidence(StoredAppliedEffectEvidence):
    pass
```

The prepared [models.py](../../examples/orders/models.py) already includes
`PrintOrder`, `PrintWork` and `PrintEvidence`. `PrintWork` captures the order's
reference and title. It uses `ObservedExternalOperationWork`, an extension of
`ExternalOperationWork`, so chapter 6 can add recovery by reading the receipt.

## 3. Define submission and outcome hooks

Here is the submission-only form of the capability. Read this definition; the
prepared app already registers the observed version shown in chapter 6.

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

mirror = StatusMirror(lambda work: PrintOrder.objects.filter(pk=work.owner_id))


class SubmitOrder(DurableExternalOperation[PrintOrder, PrintWork, PrintReceipt]):
    evidence_store = JsonAppliedEffectEvidenceStore(PrintEvidence, PrintReceipt)
    on_applied = mirror.outcome(lambda work, receipt: {"provider_order_id": receipt.order_id, "status": "printed"})
    on_unknown = mirror.outcome({"status": "unknown"})
    on_retry = mirror.outcome({"status": "retrying"})
    on_exhausted = mirror.outcome({"status": "stopped"})
    on_terminal = mirror.outcome({"status": "stopped"})

    def perform_effect(self, work: PrintWork) -> PrintReceipt:
        try:
            return provider.submit(str(work.reference), work.title)
        except provider.PrinterRejected as error:
            raise EffectNotAppliedError(str(error)) from error
```

`JsonAppliedEffectEvidenceStore` saves the typed receipt. Recovery can use saved
evidence to finish local settlement after an interruption. `StatusMirror` makes
the outcome hooks short: each declares the fields to update on the product.
The sample's `printed` status means the provider accepted the order; it does not
claim physical printing has finished.

Only the provider's definite rejection becomes `EffectNotAppliedError`, which
permits retry. A timeout after acceptance stays uncertain. Do not translate every
provider exception into permission to try again.

## 4. Submit once and lose the reply

Now use the prepared, registered instance. Its observation hook is disabled by
the provider fault below, so it cannot resolve uncertainty yet:

```python
import os

from due.django.conformance import WorkTableDelivery, hold_nudges
from due.django.public import EffectOutcomeUnknownError
from examples.orders import provider
from examples.orders.effects import print_order
from examples.orders.models import PrintOrder
from examples.orders.services import order_print

os.environ["DUE_PRINTER_LOSE_RESPONSE"] = "1"
os.environ["DUE_PRINTER_HIDE_RECEIPTS"] = "1"

with hold_nudges():
    work = order_print("Hello from Due")
    try:
        print_order.attempt_execution(work.pk)
    except EffectOutcomeUnknownError:
        print("The provider may have accepted the order")

order = PrintOrder.objects.get(pk=work.owner_id)
accepted = provider.receipt(str(work.reference))
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
