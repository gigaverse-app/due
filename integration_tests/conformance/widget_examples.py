"""Application outcomes for a DB-backed checkout, not just successful work flags."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

from django.test import override_settings
from pytest_obligation.profiles.harmless_replay import ReplaySafeEffect

from due.django.conformance.provider import ProviderProbe
from due.django.conformance.seams import ProviderSeam, seam_examples
from due.django.conformance.suite import DeliveryExample, EligibilityExample, ProducerExample, TerminalExample
from due.django.testing import hold_nudges
from examples.widget_store import services
from examples.widget_store.effects import checkout_order
from examples.widget_store.models import CheckoutWork, Order, Product

type Outcome = tuple[str, int, bool]
type Calls = ProviderProbe[[UUID, int], float | None, tuple[UUID, int]]


@contextmanager
def environment() -> Iterator[Calls]:
    probe: Calls = ProviderProbe(services.advance, key=lambda key, stage: (key, stage))
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
    calls_for=lambda calls, work: sum(calls.count((work.submission_key, stage)) for stage in range(11, 0, -1)),
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
def replay() -> Iterator[ReplaySafeEffect]:
    with environment() as calls:

        def prepare() -> CheckoutWork:
            # ARRANGE: run the real prior steps, leaving the final stage owed.
            work = make_work()
            for stage in range(11, 1, -1):
                services.advance(work.submission_key, stage)
            return work

        # EXTERNAL SEAM: the counted real PostgreSQL stage command.
        # REAL PRODUCTION: repeat the identical final dispatch stage.
        # OBSERVE: independently read the whole order outcome, not a work flag.
        yield ReplaySafeEffect(
            name="widget final dispatch stage",
            prepare=prepare,
            execute=lambda work: services.advance(work.submission_key, 1),
            expected=lambda work: (Order.Status.DISPATCHED, 0, True),
            observe=observe,
            execution_count_for=lambda work: calls.count((work.submission_key, 1)),
        )


EXAMPLES = seam_examples(
    checkout_order,
    SEAM,
    environment=environment,
    replay=replay,
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


def process_history(decision: str):
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from pytest_obligation.process_histories import ProcessHistory

    from due.django.conformance.delivery import WorkTableDelivery
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
        with environment(), WorkTableDelivery(checkout_order, rounds=12).session() as delivery:
            delivery.recover()

    return ProcessHistory(
        name=f"widget {decision} process death",
        initial=("pending", 10, 100, 0),
        expected=("dispatched", 0, 99, 1) if decision == "paid" else ("cancelled", 10, 100, 1),
        run=run,
        observe=outcome,
        recover=recover,
        death_points=("after payment receipt", "before transition", "after decision", "mid dispatch", "after dispatch")
        if decision == "paid"
        else ("after reservation", "after payment receipt", "before transition", "after decision"),
    )


from dataclasses import replace  # noqa: E402

EXAMPLES = replace(EXAMPLES, process_handoffs=(process_history("paid"), process_history("failed")))
