# 2. Own the result

[Previous: run and recover a task](quickstart.md) · [All chapters](tutorial.md)

Start with [running and recovering a background task](quickstart.md). This lesson
adds application-owned work and separate hooks for doing an operation and saving
its result.

Use this API when work belongs to an editable product. For example, a document
can be edited while its PDF is rendering. You can keep the old PDF file, but only
the current revision should become the document's published download. Due checks
which attempt may publish before it calls your completion hook. The
[document application](../../examples/documents/README.md) implements that use case.

Here we use **sleep, double a number, print, save the answer** to inspect those
hooks without file storage. The numbers serve as visible inputs and outputs for
the experiments. This is a deliberately trivial demonstration of the native API.

| Experiment | What you observe | Due concept |
| --- | --- | --- |
| Roll back the request | Neither the input change nor its work survives | Atomic admission |
| Drop its notification | Recovery still produces `42` | Durable intent and recurring recovery |
| Stop after the calculation returns | It prints twice, but eventually saves `42` | Leases and replay safety |
| Change input before the old attempt settles | Old `42` is ignored; current `14` wins | Captured revisions and guarded settlement |

The [prepared example](../../examples/numbers/README.md) supplies the Django,
PostgreSQL and Celery setup. Prepare it once, then return here. All application
code is in [models.py](../../examples/numbers/models.py) and
[effects.py](../../examples/numbers/effects.py). You can read the lesson before
running anything.

## 1. Separate doing the work from publishing its answer

`perform_effect()` does the slow work. `finalize_success()` saves the result
after Due has checked that this attempt still owns completion:

```python
from time import sleep
from due.django.public import ReplaySafeEffect
from examples.numbers.models import Calculation, CalculationWork


def double(number: int) -> int:
    sleep(0.05)
    answer = number * 2
    print(f"double({number}) = {answer}", flush=True)
    return answer


class DoubleNumber(ReplaySafeEffect[Calculation, CalculationWork, int]):
    def perform_effect(self, work: CalculationWork) -> int:
        return double(work.number)

    def finalize_success(self, work: CalculationWork, result: int) -> None:
        Calculation.objects.filter(pk=work.owner_id).update(result=result, failed=False)


calculate = DoubleNumber.for_owner(work=CalculationWork)
```

This is the core of [effects.py](../../examples/numbers/effects.py). Its additional
`finalize_retry()` hook marks the product as failed when the retry budget is
exhausted.

`perform_effect()` runs outside the settlement transaction. It may run again.
`finalize_success()` runs with owner/work locks, after Due checks the attempt's
ownership and requested revision. Put only local transactional writes there.
**Never publish the product result from `perform_effect()`**; that would bypass
the protection we are about to demonstrate.

`for_owner(work=...)` takes the owner from the work model's one-to-one relation
and uses the standard policy. You do not repeat the model relationship in a
second declaration. [Policy defaults and overrides](api-guide.md#a-product-outcome-that-needs-guarded-completion)
remain available.

## 2. Commit the request and its work together

The product row records what the user wants and sees. The work row records the
input this attempt owes, plus Due's revision, claim and recovery state:

```python
from typing import ClassVar
from django.db import models
from due.django.public import UniverseDurableWork, durable_work_schema


class Calculation(models.Model):
    number = models.IntegerField(default=0)
    result = models.IntegerField(null=True)
    failed = models.BooleanField(default=False)


class CalculationWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(Calculation, on_delete=models.SET_NULL, null=True)
    number = models.IntegerField()

    class Meta:
        indexes, constraints = durable_work_schema("number_calculation")
```

Use one application command to change the number and request its calculation:

```python
def request_number(calculation_id: int, number: int) -> CalculationWork:
    with calculate.atomic(calculation_id) as pending:
        sample = pending.locked_owner
        if sample.number != number:
            sample.number = number
            sample.result = None
            sample.failed = False
            sample.save(update_fields=["number", "result", "failed"])
        return pending.request_work(CalculationWork(number=number))
```

`atomic()` locks the product owner. `request_work()` records the captured input
in the same PostgreSQL transaction and arranges a notification **after the
outermost commit**. A changed input advances the desired revision on the same
work row. The default empty `universe` is sufficient for this single-tenant app.

Repeating the same captured input reuses that work, including an already completed
result. That is why the command clears the answer only when the number changes.
An exhausted request needs an explicit retry decision; the example's
`retry_number()` uses `pending.retry_failed()` to grant a new budget. Django Tasks
has different semantics: each `.enqueue()` creates a separate invocation.

Try rolling back the outer request:

```python
from django.db import transaction
from due.django.conformance import hold_nudges

sample = Calculation.objects.create()
with hold_nudges() as sent:
    try:
        with transaction.atomic():
            request_number(sample.pk, 21)
            raise ValueError("the rest of the request failed")
    except ValueError:
        pass

sample.refresh_from_db()
assert sample.number == 0
assert not CalculationWork.objects.filter(owner=sample).exists()
assert not sent
```

There is no committed product change without its work, and no work from a rolled
back request. Keep your own related product writes inside the same transaction.

## 3. Lose the notification, keep the obligation

The remaining snippets import the configured production instance and use local
fault controls. Run them without independent workers on this database:

```python
from due.django.conformance import WorkTableDelivery, hold_nudges
from examples.numbers.effects import calculate, request_number
from examples.numbers.models import Calculation, CalculationWork

sample = Calculation.objects.create()
with hold_nudges() as dropped:
    work = request_number(sample.pk, 21)

assert len(dropped) == 1
assert CalculationWork.objects.get(pk=work.pk).state == "ready"
assert Calculation.objects.get(pk=sample.pk).result is None

with WorkTableDelivery(calculate).session() as delivery:
    delivery.recover()

assert Calculation.objects.get(pk=sample.pk).result == 42
```

`hold_nudges()` drops the immediate post-commit notification. It does not delete
the work row. Recovery discovers the outstanding work and delivers it again.
**The notification is a prompt to work; PostgreSQL remembers the obligation.**

`WorkTableDelivery` is a test driver: it advances time and drives the registered
recovery task and actual worker task body against PostgreSQL, using controlled
transport. It does not implement a second recovery algorithm. In a running app,
Beat triggers recovery and Celery workers execute the work.

## 4. Stop after the effect, before settlement

Start a fresh request, then wrap only the arithmetic function with a fault:

```python
from unittest.mock import patch
from due.django.conformance import WorkerDied, die_after
from examples.numbers import effects

sample = Calculation.objects.create()
with hold_nudges():
    work = request_number(sample.pk, 21)

with patch.object(effects, "double", die_after(effects.double)):
    try:
        calculate.attempt_execution(work.pk)
    except WorkerDied:
        pass

assert Calculation.objects.get(pk=sample.pk).result is None
with WorkTableDelivery(calculate).session() as delivery:
    delivery.recover()
assert Calculation.objects.get(pk=sample.pk).result == 42
```

The first call prints `double(21) = 42`, then the injected worker-stop exception interrupts it
before settlement. The saved result is still `None` and its claim remains held.
After the lease expires, ordinary recovery tries it again:

```text
double(21) = 42
Attempt stopped after double returned: result=None, claim is still held
double(21) = 42
Expired claim recovered: double ran again, result=42
```

This proves why `ReplaySafeEffect` requires safe repetition. Our arithmetic and
duplicate printing are harmless. Sending money twice would not be. A claim
protects local settlement; it cannot undo an external call that already happened.
Use an [evidence-aware operation](capabilities.md#uncertainty-and-evidence) for
non-repeatable effects. The fault here simulates interruption at a precise
boundary; it does not kill an OS process.

## 5. Let the old attempt finish late

The last lesson uses `applied_snapshot()` to pause a real worker on a separate
database connection after its effect has returned, before it can settle:

```text
Worker A captures number=21, calculates 42, then pauses.
The application calls request_number(sample.pk, 7).
Worker A resumes with its old answer, 42.
Due rejects its stale settlement: the product result stays None.
Recovery runs the current revision and publishes 14.
```

The decisive part of the runnable lesson is:

```python
from due.django.conformance import applied_snapshot

sample = Calculation.objects.create()
with hold_nudges():
    work = request_number(sample.pk, 21)

with (
    hold_nudges(),
    applied_snapshot(
        calculate,
        work,
        change=lambda: request_number(sample.pk, 7),
        observe=lambda: Calculation.objects.get(pk=sample.pk).result,
    ) as race,
):
    old_attempt = race.take_snapshot()
    race.supersede()
    race.run_with_snapshot(old_attempt)

assert Calculation.objects.get(pk=sample.pk).result is None

with WorkTableDelivery(calculate).session() as delivery:
    delivery.recover()
assert Calculation.objects.get(pk=sample.pk).result == 14
```

The first assertion runs **before** the
new result is computed, so a temporary stale write cannot hide behind the later
correct answer. The application does not compare revisions in its callback;
Due checks the captured revision and claim while holding the settlement locks.

## Run it and carry the pattern into your app

After the [one-time setup](../../examples/numbers/README.md#prepare-once):

```bash
uv run python -m django shell --settings=examples.settings -c "from examples.numbers.lesson import run; run()"
```

Every step asserts its observations. Then follow the
[real worker and Beat walkthrough](../../examples/numbers/README.md#use-real-workers)
to repeat notification loss with independent processes.

Replace `double()` with your own replay-safe operation, define the captured input
fields, and publish only local product state in `finalize_success()`. Register the
capability in every process using `DurableWorkRegistration` and
`configure_durable_work` from `due.django.public`. Keep recovery running.

Build the application contract alongside those hooks. The example's
[contract bindings](../../integration_tests/conformance/number_examples.py) supply
real producer commands, arithmetic, printed output and product observations.
`effect_contract_suite` generates failure histories; independent enrollment and
executed-report checks prevent a missing suite from looking successful. The
[test commands](../../examples/numbers/README.md#run-the-application-checks) run
these checks and the live worker journey. See [conformance](conformance.md) to
adapt the contract to your operation's actual guarantees.

For ordinary task functions that do not need this owner/revision split, use the
[Django Tasks greeting walkthrough](quickstart.md). Next, keep the same admission
and completion pattern while adding repair for a mutable external view.

[Next: keep an external view current](projection-tutorial.md)
