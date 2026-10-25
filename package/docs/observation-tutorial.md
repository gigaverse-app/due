# 6. Resolve uncertainty by reading

[Previous: uncertain outcomes](external-operation-tutorial.md) · [All chapters](tutorial.md)

The previous chapter left one accepted order with an unknown local outcome.
Retrying is unsafe and waiting forever is useless, but there is a third option:
*ask*. Many providers let you look up what they did, for example by searching
for orders with your reference. `ObservedExternalOperation` adds a read-only
hook that does that. When a matching receipt turns up, it becomes
[evidence](vocabulary.md#evidence-and-receipt), and Due completes the local
order from it without submitting again.

Continue in the same shell after chapter 5's recovery step. If you closed it,
repeat that chapter's steps 4–5 to create a fresh uncertain order.

## 1. Add observation state to the work model

`ObservedExternalOperationWork` extends chapter 5's `ExternalOperationWork` with
two columns, `next_observation_at` and `observation_claimed_at`, which Due uses to
schedule repeated lookups. `observation_index()` returns
the database index recovery uses to find work due for its next lookup; append
it to the indexes from `durable_work_schema()`. The prepared work model:

```python
from typing import ClassVar

from django.db import models
from due.django.public import (
    ObservedExternalOperationWork,
    durable_work_schema,
    observation_index,
)
from examples.orders.models import PrintOrder


# Chapter 5's work base, plus the columns that schedule repeated lookups.
class PrintWork(ObservedExternalOperationWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(PrintOrder, on_delete=models.SET_NULL, null=True)
    reference = models.UUIDField()
    title = models.CharField(max_length=100)

    class Meta:
        indexes, constraints = durable_work_schema("print_order")
        # Lets recovery find unknown work that is due for its next lookup.
        indexes.append(observation_index("print_observation_ix"))
```

This is the model supplied in [models.py](../../examples/orders/models.py).
The captured reference and title let the observer validate the provider's answer.

## 2. Define the observer alongside the submission hook

This is the complete capability from [effects.py](../../examples/orders/effects.py),
using the public import module:

```python
from datetime import timedelta

from due.django.public import (
    DurableWorkPolicy,
    EffectNotAppliedError,
    JsonAppliedEffectEvidenceStore,
    ObservedExternalOperation,
    StatusMirror,
)
from examples.orders import provider
from examples.orders.models import PrintEvidence, PrintOrder, PrintWork
from examples.orders.provider import PrintReceipt

mirror = StatusMirror(lambda work: PrintOrder.objects.filter(pk=work.owner_id))


# Chapter 5's capability plus observe_effect(), a read-only lookup hook.
class OrderPrinting(ObservedExternalOperation[PrintOrder, PrintWork, PrintReceipt]):
    observation_interval = timedelta(seconds=5)  # How long to wait after a lookup finds nothing.
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

    # A receipt settles the work. None means "ask again later", never "submit again".
    def observe_effect(self, work: PrintWork) -> PrintReceipt | None:
        receipt = provider.observe(str(work.reference))
        # Accept only evidence that matches the captured request.
        if receipt is not None and (receipt.reference != str(work.reference) or receipt.title != work.title):
            raise ValueError("Printer receipt does not match the captured request")
        return receipt


print_order = OrderPrinting.for_owner(
    work=PrintWork,
    input_fields=("universe", "reference", "title"),
    policy=DurableWorkPolicy(retention=None),
)
```

| Code | Comes from | What it does |
| --- | --- | --- |
| `ObservedExternalOperation[...]` | Due | `DurableExternalOperation` from chapter 5, plus a lookup hook. |
| `observe_effect(self, work)` | A hook you implement | Asks the provider what happened, using the captured `reference`. It must only read. Return a receipt if one exists, or `None`. |
| `observation_interval = timedelta(seconds=5)` | Due setting | How long to wait before looking again after a `None`. |
| `provider.observe()` | This example | Looks up a receipt in the simulated printer by reference. |
| `DurableWorkPolicy(retention=None)` | Due policy setting | Never delete this work row: it identifies an operation that may have happened, and its evidence refers to it. Provider operations require this. |

Returning a matching receipt supplies the evidence Due needs to settle. Returning
`None` leaves the outcome unknown and schedules another lookup. It never
authorizes another submission: "not found yet" is not proof that nothing
happened. The `ValueError` guards against a receipt that belongs to a different
request; only accept evidence that matches the captured input.

The interval controls when another lookup becomes eligible. Actual delivery also
depends on the recovery cadence and available workers.

## 3. Reveal the receipt and recover the same order

Keep response loss enabled, but allow reads:

```python
os.environ["DUE_PRINTER_HIDE_RECEIPTS"] = "0"

# Recovery's lookup now finds the original receipt and settles from it.
with WorkTableDelivery(print_order).session() as delivery:
    delivery.recover()

order.refresh_from_db()
print(order.status)  # printed
assert order.provider_order_id == accepted.order_id
with provider.connection() as connection:
    count = connection.execute(
        "SELECT count(*) FROM print_orders WHERE reference = ?", (str(work.reference),)
    ).fetchone()[0]
print(count)  # 1

os.environ.pop("DUE_PRINTER_LOSE_RESPONSE", None)
os.environ.pop("DUE_PRINTER_HIDE_RECEIPTS", None)
```

The local product now names the original provider order. The row count stays one:
the observer recovered the receipt from the first submission.

## 4. Accept a provider callback through the same evidence path

Some providers deliver a receipt asynchronously. The prepared application has a
small callback command in [services.py](../../examples/orders/services.py):

```python
def receive_printer_receipt(receipt: PrintReceipt) -> bool:
    work = PrintWork.objects.filter(reference=receipt.reference, title=receipt.title).first()
    if work is None:
        return False
    # The push version of observe_effect(): record a receipt that arrived by callback.
    return print_order.record_observed_effect_evidence(work.pk, receipt)
```

`record_observed_effect_evidence()` is the push version of `observe_effect()`:
it hands Due a receipt that arrived by callback. Call it with an authenticated,
validated provider receipt. It matches the receipt to the work's captured input
and records it as evidence; ordinary recovery then performs local settlement. It
never resubmits the operation.

Run both recovery paths, including duplicate callback delivery:

```bash
uv run pytest integration_tests/examples/test_orders.py integration_tests/conformance/test_order_contract.py -v
```

The scenario checks assert the same provider order ID and exactly one provider
row after recovery. They also include a control that submits twice directly and
does produce two rows, so the simulator cannot hide accidental resubmission.

[Next: recover cleanup independently](compensation-tutorial.md)
