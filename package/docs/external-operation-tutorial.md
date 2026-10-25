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

A **provider** is the system your operation calls; here it is our SQLite simulator.
A **receipt** is a value identifying what that provider accepted. When saved and
correlated to the request, it is **evidence** Due can use to complete local work.

`PrintReceipt` is our example's data type. It uses Pydantic's `BaseModel` for
validation and serialization; it is not a Django database model or a Due class:

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

The application record is our ordinary Django `PrintOrder`, defined in
[models.py](../../examples/orders/models.py):

```python
from uuid import uuid4
from django.db import models


class PrintOrder(models.Model):
    reference = models.UUIDField(default=uuid4, unique=True, editable=False)
    title = models.CharField(max_length=100)
    provider_order_id = models.PositiveBigIntegerField(null=True)
    status = models.CharField(max_length=20, default="pending")
```

For a submission-only capability, derive your captured work model from
Due's `ExternalOperationWork`. This extends `UniverseDurableWork` and marks work
as requiring evidence about whether the effect occurred. Unresolved evidence
prevents another submission.

Due's `StoredAppliedEffectEvidence` is a separate abstract Django model with an
`attempt` relation and a JSON `result` field. Subclassing it gives this application
its own concrete receipt table. The empty body is sufficient because those
fields are inherited:

```python
from due.django.public import StoredAppliedEffectEvidence


class PrintEvidence(StoredAppliedEffectEvidence):
    pass
```

The prepared [models.py](../../examples/orders/models.py) already includes
`PrintOrder`, `PrintWork` and `PrintEvidence`. `PrintWork` captures the order's
reference and title. It uses `ObservedExternalOperationWork`, an extension of
`ExternalOperationWork`, so chapter 6 can add recovery by reading the receipt.
[Chapter 6 shows that full work-model definition](observation-tutorial.md#1-add-observation-state-to-the-work-model).
For this chapter, the three stored records have distinct roles:

| Example model | Stores |
| --- | --- |
| `PrintOrder` | What the application displays: requested title, status and accepted provider order ID |
| `PrintWork` | Captured submission input and Due's work state |
| `PrintEvidence` | The successful provider receipt associated with its Due attempt |

## 3. Define submission and outcome hooks

Here is the submission-only form of the capability. Read this definition; the
prepared app already registers the observed version shown in chapter 6.

`DurableExternalOperation[PrintOrder, PrintWork, PrintReceipt]` names the owner,
work and receipt types, following chapter 2's capability pattern. Three new Due
helpers support this kind of operation:

| Due name | Purpose |
| --- | --- |
| `JsonAppliedEffectEvidenceStore(PrintEvidence, PrintReceipt)` | Validates and serializes receipts of our `PrintReceipt` type into our `PrintEvidence` table, and reads them back for recovery |
| `StatusMirror(...)` | Selects the application record with a Django queryset; `.outcome(...)` creates a completion hook that updates that record's fields under Due's settlement checks |
| `EffectNotAppliedError` | Tells Due the provider definitely did not apply this call, allowing retry; our provider's `PrinterRejected` exception supplies that guarantee |

The `on_*` names below are Due's outcome hooks. Their dictionaries contain our
application's field names and status labels:

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

`on_applied` receives the saved receipt; `on_unknown` records uncertainty;
`on_retry` records a permitted retry; `on_exhausted` and `on_terminal` record stopped
work. The callable passed to `on_applied` builds fields from the receipt. Fixed
outcomes can supply a dictionary directly. This avoids repeating the same ORM
update in every hook. Recovery can use saved evidence to finish local settlement
after interruption.

The `PrintOrder.status` strings are our UI labels, separate from Due's work-row
state. The example's `printed` label means the provider accepted the order; it
does not claim physical printing has finished.

Only the provider's definite rejection becomes `EffectNotAppliedError`, which
permits retry. A timeout after acceptance stays uncertain. Do not translate every
provider exception into permission to try again.

## 4. Submit once and lose the reply

Now use the prepared, registered instance. Its observation hook is disabled by
the provider fault below, so it cannot resolve uncertainty yet.

`order_print()` is our application command that creates an order and admits its
work. `print_order` is the configured Due capability instance, used to execute it.
The `DUE_PRINTER_*` environment variables are **our simulator's fault switches**,
not Due settings. `EffectOutcomeUnknownError` is Due's exception reporting that
an attempted external effect has an uncertain outcome.

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
