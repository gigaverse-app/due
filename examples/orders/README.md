# Print orders: observe an uncertain result before considering another submission

The local printer commits an order in SQLite outside PostgreSQL. Each submission
creates a new order, even with the same reference. The reference is only a way
to look up the exact accepted order. `ObservedExternalOperation` retains the
uncertain attempt and uses read-only provider observation to settle the product.

Use the [shared setup](../books/README.md#run-it) and migrations. Run a dedicated
worker with response loss and unavailable receipts, plus ordinary Beat:

```bash
DUE_PRINTER_LOSE_RESPONSE=1 DUE_PRINTER_HIDE_RECEIPTS=1 \
  uv run celery -A examples.celery_app:app worker --loglevel=INFO -Q due
```

Submit from another terminal:

```bash
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django print_book 'Kindred'
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django due_status print_order
```

The order becomes UNKNOWN. Stop the worker and restart it without
`DUE_PRINTER_HIDE_RECEIPTS`; keep `DUE_PRINTER_LOSE_RESPONSE=1` to show that
recovery does not depend on another successful submission. Beat schedules a
provider read and the product becomes printed from its original receipt.

```bash
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django shell <<'PY'
from examples.orders.models import PrintOrder
from examples.orders import provider
for order in PrintOrder.objects.all():
    print(order.pk, order.status, order.provider_order_id, provider.receipt(str(order.reference)))
PY
uv run pytest tests/examples/test_orders.py tests/conformance/test_order_contract.py -v
```

`DUE_PRINTER_REJECT=1` models a definite refusal before acceptance and permits a
bounded retry. A timeout after acceptance never does. The generated contracts
exercise lost observation delivery, inconclusive reads, duplicate delivery,
competing receipts and settlement rollback. A separate provider control verifies
that blindly submitting twice really creates two orders.

The provider shares the demo's `DUE_BOOK_INDEX` SQLite file. This is a local
printer simulation with real independent commits, not a production printing
service or a claim of exactly-once network execution. There is deliberately no
operator command that clears uncertainty or orders a second copy.
