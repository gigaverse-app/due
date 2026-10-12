"""Application outcomes for a DB-backed checkout, not just successful work flags."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from uuid import uuid4

from django.test import override_settings
from pytest_obligation.completion import BoundedCompletion, DeliveryReplay

from due.django.conformance.database_replay import database_replay
from due.django.conformance.provider import ProviderProbe
from due.django.conformance.seams import ProviderSeam, seam_examples
from due.django.conformance.suite import DeliveryExample, EligibilityExample, ProducerExample, TerminalExample
from due.django.testing import hold_nudges, make_due
from examples.widget_store import services
from examples.widget_store.effects import checkout_order
from examples.widget_store.models import CheckoutWork, Order, Product

type Outcome = tuple[str, int, bool]
type Calls = ProviderProbe[[CheckoutWork], None, int]


@contextmanager
def environment() -> Iterator[Calls]:
    probe: Calls = ProviderProbe(services.advance, key=lambda work: work.pk)
    with override_settings(WIDGET_STAGE_SECONDS=0), probe.patch(services, "advance"):
        yield probe


def make_pending() -> CheckoutWork:
    with hold_nudges():
        order = services.checkout(uuid4())
    return CheckoutWork.objects.get(owner=order)


def make_work() -> CheckoutWork:
    work = make_pending()
    with hold_nudges():
        services.payment(work.submission_key, "paid")
    return work


def observe(work: CheckoutWork) -> Outcome:
    order = Order.objects.get(pk=work.owner_id)
    return order.status, order.progress_remaining, order.reserved


def rearm(work: CheckoutWork) -> None:
    assert work.owner_id is not None
    assert services.retry_order(work.owner_id)


# ARRANGE: real checkout and simulated payment commands reserve actual stock.
# REAL PRODUCTION: CheckoutOrder executes transactional services.advance.
# EXTERNAL SEAM: count the real PostgreSQL business command, or reject its entry.
# OBSERVE: independently read the order's dispatch progress and reservation.
SEAM = ProviderSeam(
    environment=environment,
    make_work=make_work,
    observe=lambda calls, work: observe(work),
    delivered=lambda calls, work: (Order.Status.PAID, 10, True),
    calls=lambda calls: calls.total,
    calls_for=lambda calls, work: calls.count(work.pk),
    refuse=lambda calls: calls.reject(lambda: OSError("database command unavailable")),
)


@contextmanager
def gate() -> Iterator[EligibilityExample[CheckoutWork, Outcome]]:
    with environment() as calls, override_settings(WIDGET_PAYMENT_TIMEOUT_SECONDS=86400 * 365):
        work = make_pending()

        def release() -> None:
            services.payment(work.submission_key, "paid")

        yield EligibilityExample(
            delivery=DeliveryExample(
                work, lambda: observe(work), (Order.Status.DISPATCHED, 0, True), lambda: calls.total
            ),
            make_eligible=release,
        )


@contextmanager
def producer() -> Iterator[ProducerExample[CheckoutWork, bool]]:
    with environment() as calls:
        key = uuid4()

        def produce() -> tuple[CheckoutWork, ...]:
            # REAL PRODUCTION: the same command the checkout endpoint uses.
            order = services.checkout(key)
            return (CheckoutWork.objects.get(owner=order),)

        def revisit(works: tuple[CheckoutWork, ...]) -> None:
            owner_id = works[0].owner_id
            assert owner_id is not None
            services.retry_order(owner_id)

        yield ProducerExample(
            produce=produce,
            observe=lambda: Order.objects.filter(submission_key=key, reserved=True).exists(),
            expected=True,
            effect_calls=lambda: calls.total,
            revisit=revisit,
        )


def perturb(work: CheckoutWork) -> None:
    # ARRANGE: edit the catalog, not the captured submission identity.
    Product.objects.filter(order__pk=work.owner_id).update(name="New catalog title")


@contextmanager
def replay(*, connection_loss: bool = False) -> Iterator[DeliveryReplay]:
    # ARRANGE: execute prior transitions, leaving final dispatch owed.
    # REAL PRODUCTION: replay the same claimed delivery through DatabaseEffect.
    # EXTERNAL SEAM: count the real database callback; substitute no outcome.
    # OBSERVE: require dispatched status, zero remaining, and one callback entry.
    with environment() as calls, hold_nudges():
        work = make_work()
        for _ in range(10):
            make_due(work)
            checkout_order.attempt_execution(work.pk)
        make_due(work)
        with database_replay(
            checkout_order,
            lambda: DeliveryExample(
                work, lambda: observe(work), (Order.Status.DISPATCHED, 0, True), lambda: calls.count(work.pk)
            ),
            connection_loss=connection_loss,
        ) as bound:
            yield bound


@contextmanager
def terminal_completion(decision: str, *, lose_hints: bool) -> Iterator[BoundedCompletion]:
    from due.django.conformance.completion import completion

    # ARRANGE: real one-second stages, same settings as the interactive demo.
    # REAL PRODUCTION: payment command, registered recovery and actual worker tasks.
    # EXTERNAL SEAM: optionally discard every timer hint; retain ordinary delivery.
    # OBSERVE: complete product outcome, inventory and monotonic dispatch progress.
    with override_settings(WIDGET_STAGE_SECONDS=1):

        def example() -> DeliveryExample:
            work = make_pending()
            with hold_nudges():
                services.payment(work.submission_key, decision)
            expected = (Order.Status.DISPATCHED, 0, 99) if decision == "paid" else (Order.Status.CANCELLED, 10, 100)

            def outcome():
                order = Order.objects.get(pk=work.owner_id)
                return order.status, order.progress_remaining, Product.objects.get(pk=order.product_id).inventory

            return DeliveryExample(work, outcome, expected)

        with completion(
            checkout_order,
            example,
            within_seconds=12,
            not_before_seconds=10 if decision == "paid" else 0,
            lose_hints=lose_hints,
            remaining=lambda work: Order.objects.get(pk=work.owner_id).progress_remaining,
        ) as bound:
            # Independent product progress, not a work-table completion flag.
            yield bound


EXAMPLES = seam_examples(
    checkout_order,
    SEAM,
    environment=environment,
    delivery_replays={
        "final dispatch": replay,
        "reconnected dispatch": partial(replay, connection_loss=True),
    },
    completions={
        f"{decision} {transport}": partial(terminal_completion, decision, lose_hints=lost)
        for decision in ("paid", "failed")
        for transport, lost in (("hints", False), ("sweep", True))
    },
    fixtures=("widget_database",),
    terminal=lambda: TerminalExample(
        observe=lambda work: Order.objects.get(pk=work.owner_id).needs_attention, rearm=rearm
    ),
    eligibility={"payment received": gate},
    immutable_inputs="A checkout captures one submission key; catalog edits and retries never replace it.",
    perturb_inputs=perturb,
    admissions={"reserve widget": producer},
    admission_failures={"reserve widget": SEAM.interrupted(producer, CheckoutWork)},
)


def process_history(decision: str, *, lose_hints: bool = False):
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from pytest_obligation.process_histories import ProcessHistory

    from due.django.conformance.delivery import WorkTableDelivery
    from due.django.runtime.tasks import wake_scheduled_work_task
    from integration_tests.harness.processes import run_child

    def run(point: str | None) -> tuple[str, int]:
        key = str(uuid4())
        with TemporaryDirectory() as directory:
            code = run_child(
                "integration_tests.harness.widget_process", f"{decision}:{key}", point, Path(directory) / "fault"
            )
        return key, code

    def outcome(key: str) -> tuple[str, int, int, int]:
        order = Order.objects.get(submission_key=key)
        return (
            order.status,
            order.progress_remaining,
            Product.objects.get(pk=1).inventory,
            CheckoutWork.objects.filter(owner=order).count(),
        )

    def recover(key: str) -> None:
        # REAL PRODUCTION: the registered recovery owner, never direct settlement.
        route = WorkTableDelivery(
            checkout_order, rounds=12, lost_tasks=(wake_scheduled_work_task,) if lose_hints else ()
        )
        with environment(), route.session() as delivery:
            delivery.recover()

    return ProcessHistory(
        name=f"widget {decision} process death" + (" without hints" if lose_hints else ""),
        initial=("pending", 10, 100, 0),
        expected=("dispatched", 0, 99, 1) if decision == "paid" else ("cancelled", 10, 100, 1),
        run=run,
        observe=outcome,
        recover=recover,
        death_points=(
            "after payment receipt",
            "before transition",
            "before decision commit",
            "after continuation commit",
            "before mid-dispatch commit",
            "before dispatch commit",
        )
        if decision == "paid"
        else ("after reservation", "after payment receipt", "before transition", "before decision commit"),
    )


from dataclasses import replace  # noqa: E402

EXAMPLES = replace(
    EXAMPLES,
    process_handoffs=tuple(
        process_history(decision, lose_hints=lose_hints)
        for lose_hints in (False, True)
        for decision in ("paid", "failed")
    ),
)
