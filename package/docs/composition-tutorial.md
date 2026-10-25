# 8. Compose and test your work

[Previous: independent cleanup](compensation-tutorial.md) · [All chapters](tutorial.md)

You now have operations with explicit completion rules. This chapter connects
them, tries short work inline, and gives your application's recovery behavior an
executable contract. Use the [prepared native shell](tutorial.md#prepare-the-native-chapters-once).

Multi-step work has its own version of the lost-message problem. Step one
finishes and then queues step two. If the process dies between those two
actions, step one looks done and step two never happens. The fix is the same as
in chapter 2: record step two as owed work in the same transaction that records
step one as finished. Due calls that a **handoff**.

## 1. Request the next stage while completing the first

Suppose the first operation creates a search entry and the second publishes it.
The handoff belongs in `finalize_success()`, so completing one stage and owing
the next stage commit together:

```python
import hashlib

from due.django.public import ReplaySafeEffect
from examples.books import search
from examples.books.models import Book, IndexBookWork, PublishBookWork


class IndexBook(ReplaySafeEffect[Book, IndexBookWork, str]):
    def perform_effect(self, work: IndexBookWork) -> str:
        key = hashlib.sha256(work.provider_idempotency_key().encode()).hexdigest()
        return search.put(search.IndexEntry(key=key, title=work.title, author=work.author))

    def finalize_success(self, work: IndexBookWork, result: str) -> None:
        assert work.owner_id is not None
        with publish_book.atomic(work.owner_id) as pending:
            pending.request_work(PublishBookWork(index_key=result, universe=work.universe))


class PublishBook(ReplaySafeEffect[Book, PublishBookWork, str]):
    def perform_effect(self, work: PublishBookWork) -> str:
        return search.publish(work.index_key)

    def finalize_success(self, work: PublishBookWork, result: str) -> None:
        Book.objects.filter(pk=work.owner_id).update(index_key=result, indexing_failed=False)


index_book = IndexBook.for_owner(work=IndexBookWork)
publish_book = PublishBook.for_owner(work=PublishBookWork)
```

These are the central hooks in [effects.py](../../examples/books/effects.py).
Everything here appeared in chapter 2 except the shape: `IndexBook`'s
`finalize_success()` calls `publish_book.atomic()` and `request_work()`, so the
second step is admitted inside the first step's settlement transaction. The
prepared classes also mark exhausted attempts in the product. As before,
external writes happen in `perform_effect()`. Finalizers contain only local
database writes and admission of further work.

Try the prepared instances with immediate notifications withheld:

```python
from due.django.conformance import WorkTableDelivery, hold_nudges
from examples.books import search
from examples.books.effects import index_book, publish_book
from examples.books.models import IndexBookWork, PublishBookWork
from examples.books.services import create_book

with hold_nudges():
    book = create_book("Hello from Due", "Author")
    index_book.attempt_execution(IndexBookWork.objects.get(owner=book).pk)

next_work = PublishBookWork.objects.get(owner=book)
print(search.get(next_work.index_key).published)  # False

with WorkTableDelivery(publish_book).session() as delivery:
    delivery.recover()

book.refresh_from_db()
print(search.get(book.index_key).published)  # True
```

The successor exists even though its notification was lost. Recovery discovers
it from its own work table. A successful first stage cannot commit without that
successor obligation.

## 2. Try short work inline, while retaining recovery

So far, work ran in a worker after a nudge. For quick work, you may prefer to
run it right away in the request, so the user sees the result immediately, but
without losing recovery if that inline attempt is interrupted. The catalog
command from chapter 3 does this with `complete_or_defer()` after committing the
request. Here is its definition:

```python
from examples.catalog.effects import project_catalog
from examples.catalog.models import CatalogWork
from examples.catalog.services import save_entry


def save_and_project_entry(title: str, author: str, *, entry_id: int | None = None) -> CatalogWork:
    work = save_entry(title, author, entry_id=entry_id, notify_worker=False)
    project_catalog.complete_or_defer(work)
    return work
```

The method attempts completion immediately when it is safe to do so. Inside an
outer database transaction it defers execution until after commit instead.
The committed obligation remains recoverable if inline execution is interrupted.

Use `.complete(work)` when the caller needs completion before proceeding. It
requires a committed transaction boundary and raises `WorkIncomplete` when it
cannot establish completion for the requested work. Handle that result explicitly;
it does not mean the external operation definitely failed.

## 3. Refresh completed work or grant a retry

`wake()` requests another execution of the current captured input. The catalog's
refresh command uses it when an external search entry needs to be rebuilt:

```python
from examples.catalog.services import refresh_entry, save_and_project_entry

work = save_and_project_entry("A title to refresh", "Author")
refresh_entry(work.owner_id)
```

The command's core is:

```python
project_catalog.wake(work.pk, notify_worker=False)
project_catalog.complete_or_defer(work)
```

An exhausted operation needs an explicit new retry budget. The arithmetic example
grants one under the owner lock:

```python
with calculate.atomic(calculation_id) as pending:
    retried = pending.retry_failed()
```

The [numbers commands](../../examples/numbers/effects.py) also clear the displayed
failure when that retry is accepted. Keep retry decisions in a product command
so work state and user-visible state agree.

## 4. Bind tests to your actual hooks and observations

The experiments in chapter 2 (roll back, drop a nudge, die after the effect,
finish late) apply to every capability. Due can generate them as pytest tests
for your own capability, so you do not write each failure scenario by hand.
Due calls this an **application contract**.

Due supplies the failure histories. Your application supplies their meaning: how
to create work, invoke the real provider, and observe the product and external
result independently. These descriptions are called **bindings**. For the
arithmetic example, they are simple:

```python
from examples.numbers.effects import request_number
from examples.numbers.models import Calculation, CalculationWork


def make_work() -> CalculationWork:
    return request_number(Calculation.objects.create().pk, 21)


def product(work: CalculationWork) -> int | None:
    return Calculation.objects.get(pk=work.owner_id).result
```

The complete [binding file](../../integration_tests/conformance/number_examples.py)
also counts calls to the real `double()`, captures its printed output, invokes the
real retry command, and requests changed input for the stale-result history.
It assembles those bindings with `ProviderSeam` and `seam_examples`.

`ProviderSeam` and `seam_examples` are the `due.django.conformance` helpers that
bundle those bindings into `EXAMPLES`; the [conformance guide](conformance.md)
describes them. `effect_contract_suite` then turns a capability and its bindings
into a pytest test class. The suite declaration stays small:

```python
from due.django.conformance import effect_contract_suite
from examples.numbers.effects import calculate
from integration_tests.conformance.number_examples import EXAMPLES


@effect_contract_suite(calculate, examples=EXAMPLES)
class TestNumberCalculation:
    pass
```

Use the prepared [test module](../../integration_tests/conformance/test_number_contract.py),
which also installs its provider fixture, and run:

```bash
uv run pytest integration_tests/conformance/test_number_contract.py -v
uv run pytest integration_tests/conformance/test_book_contracts.py -v
```

The generated tests exercise rollback, delivery loss, interruption, retry limits
and stale completion where applicable. The repository separately checks that
each registered capability is enrolled and that required proofs actually ran.
For your application, follow [the conformance guide](conformance.md) to add that
host fixture, enrollment check and executed-report gate.

## Apply the capabilities to your own project

Choose a capability for the operation you actually have, define its captured input
and hooks, register it once in startup, and bind its application contract. Use
`due.django.public` for the application API and `due.django.conformance` for the
test API. The [API guide](api-guide.md) maps the choices and common imports.

For additional controls such as execution eligibility, discovery, continued work,
custom retry delays and asynchronous provider calls, use the
[capabilities reference](capabilities.md) alongside the
[integration guide](integration.md). The chapters above establish the operation,
completion and recovery boundaries those controls build on.
