# 2. Own the result

[Previous: run and recover a task](quickstart.md) · [All chapters](tutorial.md)

In chapter 1, each enqueue created a job with a saved return value. Here, the
result belongs to a record in your application, and that record can change while
work is running. We use Due's own work-model API, called the **native API** in
these docs. For example, a document can be edited while its PDF is rendering.
Only the current revision should become the document's published download. The
[document application](../../examples/documents/README.md) implements that use case.

We will use **sleep, double a number, print, save the answer** to make the same
execution and completion steps visible. The arithmetic is deliberately trivial
so you can focus on Due.

There are three pieces to build:

| Our example code | What it is | What it is for |
| --- | --- | --- |
| `Calculation` | An ordinary Django model | The requested number and the answer the user sees |
| `CalculationWork` | Our model built on Due's work-model base | Captured input plus Due's status, revision and recovery bookkeeping |
| `DoubleNumber`, bound as `calculate` | Our capability class and its configured instance | Hooks that do the calculation and save an authorized result |

A **capability** combines your hooks with Due's execution and recovery rules.
A **hook** is a method you implement and Due calls at a particular point.
Neither is a Django model or a queued job.

The prepared [models.py](../../examples/numbers/models.py) and
[effects.py](../../examples/numbers/effects.py) contain the definitions below.
Read those definitions in steps 1–4; the shell exercises import the configured
example. Use the [shared native setup](tutorial.md#prepare-the-native-chapters-once).

## 1. Define the application record and its work row

Due calls the application record the work's **owner**. Here, owner means the
`Calculation` record the work belongs to, not a user account or a worker process.

`UniverseDurableWork` is **Due's abstract Django model**. Subclassing it puts
Due's coordination fields in our concrete `CalculationWork` table. We add our
captured input, `number`, and a Django relation to `Calculation`.

Two Due declarations connect this model to the runtime: `work_owner_field` names
the owner relation, and `durable_work_schema()` supplies the indexes and
constraints required for a work table. Here is the complete example model file:

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

Read the declarations by where they come from:

| Declaration | Comes from | What it means here |
| --- | --- | --- |
| `models.Model`, `IntegerField`, `OneToOneField` | Django | Ordinary models, fields and relations |
| `UniverseDurableWork` | Due | Adds stored work state, revision counters, claim/lease fields and execution context |
| `work_owner_field = "owner"` | Due's model convention | Tells Due to follow the field named `owner` when locking the application record and completing its work |
| `ClassVar[str]` | Python typing | Marks that string as class configuration; it is not a database column |
| `owner`, `number`, `Calculation`, `CalculationWork` | Our example | Names and data we chose; `work_owner_field` must match our relation's name |
| `class Meta`, `indexes`, `constraints` | Django | Database schema options |
| `durable_work_schema("number_calculation")` | Due | Returns the two lists assigned to Django's schema options |

Claims and leases control attempts to run this work; they are explained in
[step 2](#2-define-the-operation-and-its-completion-hook). You do not populate
those coordination fields yourself.

For example, the schema helper adds indexes for finding ready work and expired
leases. Its constraints require a claimed row to have a lease expiry and a
successful row to identify the revision it completed. Django migrations install
these definitions in PostgreSQL. `"number_calculation"` is our chosen prefix for
the database index and constraint names. Choose a distinct, stable prefix for
each concrete work model; it is not a queue name or a function to execute.

Why **Universe**? The base also supplies a `universe` string: a persisted context
identifier applications can use for a tenant or workspace. Due restores that
execution context when running work. This example uses the default empty string.
You do not need another model or any context setup here. See
[application context hooks](integration.md) when your application needs them.

The two `number` fields are intentional. `Calculation.number` describes the
application's current request. `CalculationWork.number` is the input captured for
execution. Due also stores a **desired revision**, a counter advanced when that
requested input changes. It uses the revision to recognize an old attempt's result.

## 2. Define the operation and its completion hook

`ReplaySafeEffect` is Due's capability base for an operation that is safe to run
again after interruption. An **effect** is the work performed, such as calculating
a value or writing a file. **Replay-safe** is a promise your implementation must
satisfy; inheriting this class does not make arbitrary calls safe to repeat.

Our ordinary Python function, `double()`, sleeps, multiplies and prints. The
example implements Due's `perform_effect()` hook to call that function and
`finalize_success()` to save the authorized result. The final `for_owner()` call
binds those hooks to our models, producing a configured instance named `calculate`.
Here is the core of the example's `effects.py`:

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

`DoubleNumber` is our class; `ReplaySafeEffect` supplies its protocol and hook
names. The bracketed types are Python generic parameters, in order: the owner
model (`Calculation`), work model (`CalculationWork`), and result type (`int`).

| Hook | When Due calls it | What our implementation does |
| --- | --- | --- |
| `perform_effect(work)` | After claiming an attempt, outside the completion transaction | Reads captured input, calls `double()`, returns an integer |
| `finalize_success(work, result)` | After checking that the attempt and requested revision are still current, with owner/work locks held | Uses the Django ORM to save the answer on `Calculation` |

A **claim** is an attempt's temporary right to execute and complete work. It has
a unique token and a **lease**, the time allowed before recovery can reclaim it.
**Settlement** means checking that claim and revision, then committing completion
and your finalizer's local writes together. These checks are also called
**fencing**: an old attempt cannot save completion after losing that right.

`work.owner_id` comes from Django's `owner` relation. The
`.objects.filter().update()` call is ordinary Django code. Due decides **when the
hook is allowed to run**. Put local transactional writes there; keep slow work in
`perform_effect()`. Saving `Calculation.result` from `perform_effect()` would
bypass the completion check.

### What does `for_owner()` create?

`for_owner()` is Due's factory for a configured capability instance. It reads
`CalculationWork.work_owner_field`, follows that relation to `Calculation`,
and connects the hooks to those models. Its default `DurableWorkPolicy` supplies
lease, retry and retention settings; [defaults and overrides](api-guide.md#a-product-outcome-that-needs-guarded-completion)
are covered separately.

`calculate` is that Python instance, used by application commands and the worker
configuration. Creating it does not insert a database row or run `double()`.
You bind it once; the next step creates actual work through its methods.

The prepared class also defines `finalize_retry()`, another Due hook, to set our
`failed` field when the retry budget is exhausted. Failure status and explicit
retry are covered in [chapter 8](composition-tutorial.md#3-refresh-completed-work-or-grant-a-retry).

## 3. Request work from an application command

**Admission** is Due's name for recording a request as durable work.
`calculate.atomic(id)` locks the owner and opens the transaction for admission.
It yields a Due helper, which we name `pending`. Its `locked_owner` is the
`Calculation` record. Its `request_work()` method saves captured input and
arranges worker notification after commit.

`request_number()` is our application function, defined in the same `effects.py`:

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

`CalculationWork(number=number)` constructs an unsaved Django model instance
containing the requested input. Due's `request_work()` creates or updates the
linked work row and returns it. You do not call `.save()` on that proposed work
yourself. The application change and its work share one transaction. The
notification runs **after the outermost commit**.

Repeating the same captured input reuses the work, including an already completed
result. That is why this function clears the answer only when the number changes.
Changed input advances the desired revision on the same work row. An exhausted
request needs an explicit retry decision, covered in chapter 8. Each Django Tasks
`.enqueue()`, by comparison, creates a separate invocation.

### Try one request

In the prepared Django shell, import the example's definitions and Due's
`hold_nudges()` test helper. A **nudge** is the notification that tells a worker
there may be work ready. This helper captures and withholds it so we can inspect
the request before anything executes.

`complete(work)` is Due's method for executing already-admitted work in this
process and requiring its completion. It uses the same claim and completion
checks as a worker. It must run after the admission transaction commits.
[Chapter 8](composition-tutorial.md#2-try-short-work-inline-while-retaining-recovery)
covers incomplete outcomes and deferring to workers. Try it:

```python
from due.django.conformance import hold_nudges
from examples.numbers.effects import calculate, request_number
from examples.numbers.models import Calculation, CalculationWork

sample = Calculation.objects.create()
with hold_nudges():
    work = request_number(sample.pk, 21)

print(sample.pk == work.owner_id)  # True: this work belongs to this calculation.
print(work.number, work.state)  # 21 ready

calculate.complete(work)
sample.refresh_from_db()
print(sample.result)  # 42
```

The sequence is: our command records input, Due calls `perform_effect()`, our
function returns `42`, then Due checks the attempt and calls `finalize_success()`
to save that answer.

### Roll back the request

Now reject a fresh request inside an outer Django transaction:

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

There is no committed application change without its work, and no work from a
rolled-back request. `transaction.atomic()` in this exercise is Django's
transaction context; `calculate.atomic()` in our command adds Due's owner locking
and admission helper inside it.

## 4. Connect the capability to workers and recovery

The prepared app already registers `calculate` at startup. Registration tells
Due which configured capability handles a named kind of work, and how often to
schedule recovery. These are the relevant Due declarations from its setup:

```python
from due.django.public import DurableWorkRegistration, configure_durable_work
from examples.numbers.effects import calculate

registration = DurableWorkRegistration(name="calculate_number", capability=calculate, cadence_seconds=5)
configure_durable_work([registration])
```

`DurableWorkRegistration` holds that configuration; `configure_durable_work()`
installs it. In your app, do this in Django's `AppConfig.ready()`, after model
loading, and run the worker and Beat processes from the setup guide. The prepared
app combines this entry with its other examples in
[the registry](../../examples/registry.py); you do not need to register it again.

The registration name `"calculate_number"` identifies the capability in worker
routing and recovery. The earlier schema prefix `"number_calculation"` names
database indexes and constraints. Those are separate configuration values.

For the remaining exercises keep independent workers stopped: Due's test driver
will control delivery and time so the failure boundaries are observable.

## 5. Lose the notification, keep the obligation

`WorkTableDelivery` is a Due test driver. It advances a controlled clock and runs
the registered recovery and worker task bodies against PostgreSQL. Its
`.session()` scopes those controls; `.recover()` drives the recovery rounds.
Use it with the same configured `calculate` instance:

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

In a running app, Beat triggers recovery and Celery workers execute the work.
The test driver invokes those same runtime paths with controlled transport.

## 6. Stop after the effect, before settlement

`die_after()` is a Due test helper: it calls our real function, then raises
`WorkerDied` to simulate interruption before settlement. `patch` is Python's
standard-library replacement helper. We replace only our `double()` function:
Due's admission, execution and completion logic still runs.

`attempt_execution(work.pk)` is Due's lower-level method for making one execution
attempt by work-row ID. Here we call it directly to observe the interruption;
`complete()` from step 3 additionally requires a completed outcome.

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

## 7. Let the old attempt finish late

The last lesson introduces Due's `applied_snapshot()` test helper. It pauses a
real worker on a separate database connection after its effect has returned,
before it can settle:

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
The [contract section in chapter 8](composition-tutorial.md#4-bind-tests-to-your-actual-hooks-and-observations)
introduces these bindings, generated tests and the checks that prevent a missing
test suite from looking successful. The
[test commands](../../examples/numbers/README.md#run-the-application-checks) run
these checks and the live worker journey. See [conformance](conformance.md) to
adapt the contract to your operation's actual guarantees.

For ordinary task functions that do not need this owner/revision split, use the
[Django Tasks greeting walkthrough](quickstart.md). Next, keep the same admission
and completion pattern while adding repair for a mutable external view.

[Next: keep an external view current](projection-tutorial.md)
