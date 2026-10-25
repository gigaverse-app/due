# 2. Own the result

[Previous: run and recover a task](quickstart.md) · [All chapters](tutorial.md)

In [chapter 1](quickstart.md) every job stood alone: it received arguments and
returned a value. Real background work usually belongs to something in your app.
A PDF is rendered *for a document*. A thumbnail is made *for an upload*. And the
user can change that thing while the work is still running.

That is where background work quietly goes wrong:

- The user edits a document while its PDF renders. Rendering the new version
  is quick, the old one is slow, and the old PDF finishes last and replaces the
  new one.
- A worker looks dead, so another worker starts the same render. Both finish.
- The request saves the document, but the message to start rendering is lost.
  Nothing fails; the PDF is just never made.

This chapter uses Due's own API to rule those out. The work stays deliberately
trivial: **sleep, double a number, print, save the answer**. Doubling stands in
for rendering; the number stands in for the document. The
[document application](../../examples/documents/README.md) applies the same
pattern to real files once you know it.

You will run four experiments:

| Experiment | What you will observe | The idea behind it |
| --- | --- | --- |
| Roll back the request | Neither the new number nor its work survives | [Atomic admission](vocabulary.md#admission) |
| Lose the message that starts the work | Recovery still produces `42` | [Owed work](vocabulary.md#owed-work-and-the-work-row) and [recovery](vocabulary.md#recovery) |
| Stop the worker after the calculation returns | It prints twice, but saves `42` once | [Leases](vocabulary.md#attempt-claim-and-lease) and [replay safety](vocabulary.md#replay-and-replay-safe) |
| Change the number while an old attempt is still running | The old `42` is refused; the current `14` wins | [Revisions](vocabulary.md#revision), [fencing](vocabulary.md#fencing-token) and [settlement](vocabulary.md#settlement) |

The [prepared example](../../examples/numbers/README.md) supplies the Django,
PostgreSQL and Celery setup; prepare it once with the
[native chapter setup](tutorial.md#prepare-the-native-chapters-once). All of the
application code is in two short files,
[models.py](../../examples/numbers/models.py) and
[effects.py](../../examples/numbers/effects.py). You can read the whole lesson
before running anything.

## 1. Two rows: what the user sees, and what is owed

Due keeps two separate records for every piece of work:

- the **product row**, your own model, which holds what the user sees; and
- the **work row**, which holds the input the work needs and Due's bookkeeping
  about running it.

### The product row is plain Django

```python
from django.db import models


class Calculation(models.Model):
    number = models.IntegerField(default=0)
    result = models.IntegerField(null=True)
    failed = models.BooleanField(default=False)
```

There is nothing from Due here. `number` is what the user asked for, `result` is
the published answer, and `failed` lets the app show that the work gave up. Due
calls this row the **owner** of the work.

### The work row is a Django model built on a Due base class

Why not put the work's state on `Calculation`? Because the two change for
different reasons. The user changes `number` whenever they like. The work needs a
stable copy of the number it was asked to double, plus a record of which worker
is running it, until when, and on which version of the input. Keeping those
apart is what lets Due tell a current attempt from an outdated one.

```python
from typing import ClassVar

from django.db import models
from due.django.public import UniverseDurableWork, durable_work_schema


class CalculationWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(Calculation, on_delete=models.SET_NULL, null=True)
    number = models.IntegerField()

    class Meta:
        indexes, constraints = durable_work_schema("number_calculation")
```

Line by line:

| Code | Comes from | What it does |
| --- | --- | --- |
| `UniverseDurableWork` | Due | An abstract Django model. Inheriting it adds Due's bookkeeping columns to your table: `state`, `lease_expires_at`, `fencing_token`, `desired_revision`, `applied_revision`, `attempt_count` and a few more. You never write those columns yourself. It also adds a `universe` column for apps serving several tenants; a single-tenant app leaves it empty and can ignore it. |
| `owner` | Django | Links each work row to its product row. *One-to-one* means a calculation has at most one work row: when the user asks for a new number, Due updates that row instead of adding another. `on_delete=SET_NULL, null=True` means deleting the product leaves the work row and its history in place, rather than deleting a row a worker may be holding. |
| `work_owner_field` | Due | Tells Due which field is the owner relation. Due locks the owner row through it before recording or saving work, so a request and a worker never update the same calculation at once. It is a `ClassVar`, so Django does not treat it as a column. |
| `number` | This example | The **captured input**: a copy of the number taken when the work was requested. The worker doubles this copy, not whatever `Calculation.number` says by then. Any field you add (other than the owner) is captured input. |
| `durable_work_schema(...)` | Due | Returns the indexes recovery uses to find owed work quickly, and the check constraints that keep the bookkeeping columns consistent. The string is a prefix for their names; use a different one for each work model. They become part of your migrations like any other index. |

You create the table with `makemigrations` and `migrate`, as for any model. The
example ships its migration.

## 2. Describe the work in two hooks

A **capability** is the object that ties a kind of work together: the work model,
its owner, and the code to run. Your app calls it to request work; Due's workers
and recovery call it to run and save that work. You write it as a class with a
few methods, called **hooks**, that Due calls at the right moments.

The work has two halves, and Due gives each its own hook:

- `perform_effect()` **does** the work. Here, it doubles the number.
- `finalize_success()` **saves** the answer to the product row.

Due runs something important between them. Here is the core of
[effects.py](../../examples/numbers/effects.py):

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

| Code | Comes from | What it does |
| --- | --- | --- |
| `double()` | This example | The actual work. Replace it with your own slow, [replay-safe](vocabulary.md#replay-and-replay-safe) operation. |
| `ReplaySafeEffect[Calculation, CalculationWork, int]` | Due | The base class for work that is safe to run more than once. Its three type parameters are the owner model, the work model, and the type `perform_effect()` returns and `finalize_success()` receives. |
| `perform_effect(self, work)` | A hook you implement | The **effect**. Due calls it with the work row and runs it outside any database transaction, so it can be slow. It can also be interrupted halfway, or run again, so it must not save anything the user sees. |
| `finalize_success(self, work, result)` | A hook you implement | **Settlement**. Due calls it inside a short transaction, with the owner and work rows locked, and only after checking that this attempt is still the current one (next paragraph). It should only write to the database. |
| `DoubleNumber.for_owner(work=CalculationWork)` | Due | Builds the capability. It finds the owner model through `work_owner_field` and applies the standard policy: a five-minute lease, a one-minute retry delay, three retries. The result, `calculate`, is the object the rest of the chapter uses. |

What does "still the current one" mean? When a worker starts an attempt, Due
gives the work row a fresh random [fencing token](vocabulary.md#fencing-token)
and hands the worker a copy, along with the [revision](vocabulary.md#revision) of
the input it is working on. Before calling `finalize_success()`, Due checks both
against the locked row. If another attempt has claimed the row since (because
this one's lease expired), or the user has asked for a different number, the
check fails and `finalize_success()` is not called. The outdated answer is
dropped.

That check protects only what happens in `finalize_success()`. **Never save the
product result from `perform_effect()`**; it runs before the check, so an
outdated attempt could overwrite a newer answer from there.

The full file also has a `finalize_retry()` hook, which marks the calculation as
failed once its retries run out. The [policy reference](api-guide.md#a-product-outcome-that-needs-guarded-completion)
shows how to change the standard policy.

## 3. Request the work together with the product change

Your app requests work through the capability, inside the transaction that
changes the product:

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

| Code | Comes from | What it does |
| --- | --- | --- |
| `calculate.atomic(calculation_id)` | Due | Opens a database transaction (or joins the one you are already in) and locks the `Calculation` row. Use it wherever your app changes the product and needs work done. |
| `pending.locked_owner` | Due | The locked `Calculation`, ready to change and save. |
| `pending.request_work(...)` | Due | **Admission**. You pass an unsaved work row holding the captured input; Due saves it in the same transaction and returns the saved row. It also arranges a [nudge](vocabulary.md#nudge) to a worker, sent only after the outermost transaction commits. |

`request_work()` compares the captured input with the existing work row for
this calculation:

- **No work row yet:** it creates one, at revision 1.
- **Same input as before:** it reuses the existing row, including a result that
  already completed. Asking for the same number twice does no extra work.
- **Different input:** it updates the row, increases its revision, and makes it
  runnable again. Any attempt still running on the old revision can no longer
  save its answer.

That is why `request_number()` clears `result` only when the number changes:
otherwise it would erase an answer that is still correct and will not be
recalculated. This differs from chapter 1, where each `.enqueue()` created a
new job even with identical arguments. Work that ran out of retries needs an
explicit decision to try again; the example's `retry_number()` calls
`pending.retry_failed()` for that.

## 4. Experiment: roll back the request

The remaining blocks are shell exercises. Start the
[prepared shell](tutorial.md#prepare-the-native-chapters-once) and keep workers
stopped.

The exercises use a few tools from `due.django.conformance`, Due's testing
package. They simulate crashes and lost messages at exact points; production
code never imports them. The first is `hold_nudges()`: inside its `with` block,
nudges are recorded in a list instead of being sent to Celery.

```python
from django.db import transaction
from due.django.conformance import hold_nudges
from examples.numbers.effects import request_number
from examples.numbers.models import Calculation, CalculationWork

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

The outer transaction rolled back, so the new number, the work row and the nudge
all disappeared together. There is no product change without its work, and no
work for a request that failed. Keep your own related writes inside the same
transaction to get the same guarantee.

## 5. Experiment: lose the nudge, keep the work

Now let the request commit, but drop its nudge, as if the broker restarted right
after the commit:

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

After the commit, one nudge was dropped and nothing ran. The work row sits in
state `ready`: owed, and waiting. **The nudge only asks a worker to hurry;
PostgreSQL remembers that the work is owed.**

`WorkTableDelivery` is the second test tool. It replays what Beat and the
workers would do, in this process and without waiting: each `recover()` moves a
simulated clock forward past leases and retry delays, runs Due's real recovery
task, and runs the work it finds through Due's real worker code. It does not
contain a second recovery algorithm. In a running app, Beat triggers recovery
and Celery workers execute the work.

## 6. Experiment: stop after the effect, before settlement

This time the worker dies at the worst moment: `double()` has returned its
answer, but the answer has not been saved.

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

| Code | Comes from | What it does |
| --- | --- | --- |
| `calculate.attempt_execution(work.pk)` | Due | Runs one attempt in this process, exactly as a worker would: claim the row, call `perform_effect()`, then settle. |
| `die_after(effects.double)` | Due test tool | Wraps `double()` so its first call runs normally and then raises `WorkerDied`, which stands in for the process dying right after the call returned. |
| `patch.object(...)` | Python's `unittest.mock` | Swaps the wrapped `double()` in for the duration of the block. |

The output shows the calculation running twice:

```text
double(21) = 42
Attempt stopped after double returned: result=None, claim is still held
double(21) = 42
Expired claim recovered: double ran again, result=42
```

The first attempt computed `42` and died before settlement, so the result stayed
`None` and its claim remained on the row. Once the lease expired, recovery gave
the work to a new attempt, which ran `double()` again and saved `42`.

This is why the base class is called `ReplaySafeEffect`: recovery reruns the
effect. Doubling and printing twice are harmless. Sending money twice is not. A
claim protects the save at the end; it cannot undo an external call that already
happened. For work that must not repeat, use the
[uncertain-outcome capabilities](external-operation-tutorial.md) in chapters 5
and 6. (The fault here interrupts the attempt at a precise point; it does not
kill an operating system process. Chapter 1 did that.)

## 7. Experiment: let an old attempt finish late

The last experiment is the stale-result problem from the start of this chapter.
The story:

```text
Worker A captures number=21, calculates 42, then pauses.
The application calls request_number(sample.pk, 7).
Worker A resumes with its old answer, 42.
Due refuses its settlement: the product result stays None.
Recovery runs the current revision and publishes 14.
```

`applied_snapshot()` is the test tool that stages it. It runs a real worker
attempt on a separate database connection and pauses it after
`perform_effect()` returns, before settlement:

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

| Code | What it does |
| --- | --- |
| `race.take_snapshot()` | Runs worker A until its `perform_effect()` returns `42`, then holds it there |
| `race.supersede()` | Calls `change`: the user asks for 7, which raises the work row's revision |
| `race.run_with_snapshot(old_attempt)` | Lets worker A continue into settlement with its old answer |

The first `assert` runs **before** the new answer is computed. So it shows that
`42` was never saved, not merely that `14` later replaced it. Notice that
`finalize_success()` contains no revision check of its own: Due compared the
attempt's revision and fencing token with the locked row and refused to call it.

## Run all four experiments

After the [one-time setup](../../examples/numbers/README.md#prepare-once):

```bash
uv run python -m django shell --settings=examples.settings -c "from examples.numbers.lesson import run; run()"
```

Every step asserts what it observes. Then follow the
[real worker and Beat walkthrough](../../examples/numbers/README.md#use-real-workers)
to repeat the lost-nudge experiment with independent processes.

## What you learned

| Name | Kind | Meaning |
| --- | --- | --- |
| `UniverseDurableWork` | Due base model | Adds Due's bookkeeping columns to your work model |
| `work_owner_field`, `owner` | Due setting, Django field | Link one work row to one product row |
| `durable_work_schema()` | Due helper | Indexes and constraints every work table needs |
| `ReplaySafeEffect` | Due capability base | Work that may safely run again |
| `perform_effect()` / `finalize_success()` | Your hooks | Do the work outside a transaction / save it after Due's check |
| `.for_owner()` | Due | Build the capability with the standard policy |
| `.atomic()`, `.locked_owner`, `.request_work()` | Due | Change the product and record owed work in one transaction |
| `hold_nudges`, `WorkTableDelivery`, `die_after`, `applied_snapshot` | Due test tools | Drop nudges, drive recovery, inject a crash, stage a late attempt |

## Carry the pattern into your app

Replace `double()` with your own replay-safe operation, put its input fields on
your work model, and save only local product state in `finalize_success()`.

Workers, Beat and your web processes must all know about the capability. You
**register** it once at startup: list it in a `DurableWorkRegistration` and pass
the list to `configure_durable_work`, both from `due.django.public`. The prepared
example does this in [examples/registry.py](../../examples/registry.py); the
[setup guide](django-tasks-quickstart.md) walks through it for your own project.
Keep Beat running so recovery runs.

Due can also generate failure tests for your capability: rollback, lost nudges,
interrupted attempts, stale results. You describe how to create work and how to
observe the result; Due supplies the failure histories. The example's
[contract bindings](../../integration_tests/conformance/number_examples.py) do
this for the calculation, and [chapter 8](composition-tutorial.md#4-bind-tests-to-your-actual-hooks-and-observations)
explains how to write them.

Ordinary task functions that never need a link to a product row can stay with the
[Django Tasks API from chapter 1](quickstart.md). Next, the same request and
settlement pattern meets an external system that can be left showing an old
value.

[Next: keep an external view current](projection-tutorial.md)
