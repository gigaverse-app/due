"""Printer contracts count actual requests; provider controls never settle Due work."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from unittest.mock import patch

from due.django.conformance.observations import ObservationExample
from due.django.conformance.provider import ProviderProbe
from due.django.conformance.suite import (
    AdmissionFailureExample,
    DeliveryExample,
    EffectExamples,
    FailureExample,
    ProducerExample,
    RejectionExample,
    TerminalExample,
)
from due.django.testing import hold_nudges
from due.errors import WorkFailureDisposition
from examples.orders import provider
from examples.orders.models import PrintOrder, PrintWork
from examples.orders.provider import PrintReceipt
from examples.orders.services import order_print
from tests.examples.books_support import index_environment


@dataclass
class PrinterCalls:
    submits: ProviderProbe[[str, str], PrintReceipt, str]
    reads: ProviderProbe[[str], PrintReceipt | None, str]


calls: ContextVar[PrinterCalls] = ContextVar("printer_calls")


@contextmanager
def environment() -> Iterator[None]:
    observed = PrinterCalls(
        submits=ProviderProbe(provider.submit, key=lambda reference, title: reference),
        reads=ProviderProbe(provider.observe, key=lambda reference: reference),
    )
    token = calls.set(observed)
    try:
        with (
            index_environment(),
            observed.submits.patch(provider, "submit"),
            observed.reads.patch(provider, "observe"),
        ):
            yield
    finally:
        calls.reset(token)


def make_work() -> PrintWork:
    with hold_nudges():
        return order_print("A physical book")


def delivery() -> DeliveryExample[PrintWork, str]:
    work = make_work()
    return DeliveryExample(
        work=work,
        observe=lambda: PrintOrder.objects.get(pk=work.owner_id).status,
        expected="printed",
        effect_calls=lambda: calls.get().submits.count(str(work.reference)),
    )


@contextmanager
def retry() -> Iterator[FailureExample[PrintWork]]:
    with calls.get().submits.reject(lambda: provider.PrinterRejected("Printer refused before acceptance")):
        yield FailureExample(attempts=lambda work: calls.get().submits.count(str(work.reference)))


@contextmanager
def rejected() -> Iterator[RejectionExample[PrintWork, str]]:
    with retry():
        yield RejectionExample(
            delivery=replace(delivery(), expected="retrying"), disposition=WorkFailureDisposition.RETRYABLE
        )


@contextmanager
def uncertainty() -> Iterator[DeliveryExample[PrintWork, str]]:
    with (
        calls.get().submits.lose_response(lambda: TimeoutError("Printer response lost after acceptance")),
        patch.dict(os.environ, {"DUE_PRINTER_HIDE_RECEIPTS": "1"}),
    ):
        yield replace(delivery(), expected="unknown")


@contextmanager
def observation() -> Iterator[ObservationExample[PrintWork, str, PrintReceipt]]:
    with uncertainty() as uncertain:

        def reveal() -> PrintReceipt:
            os.environ["DUE_PRINTER_HIDE_RECEIPTS"] = "0"
            result = provider.receipt(str(uncertain.work.reference))
            assert result is not None
            return result

        yield ObservationExample(
            delivery=replace(uncertain, expected="printed"),
            reveal=reveal,
            observation_calls=lambda: calls.get().reads.count(str(uncertain.work.reference)),
        )


@contextmanager
def producer() -> Iterator[ProducerExample[PrintWork, bool]]:
    with environment():
        yield ProducerExample(
            produce=lambda: (order_print("A physical book"),),
            observe=lambda: PrintOrder.objects.filter(title="A physical book").exists(),
            expected=True,
            effect_calls=lambda: calls.get().submits.total,
        )


@contextmanager
def admission_failure() -> Iterator[AdmissionFailureExample[PrintWork, bool]]:
    with producer() as scenario:
        yield AdmissionFailureExample.after_save(scenario, PrintWork)


EXAMPLES = EffectExamples(
    fixtures=("printer",),
    environment=environment,
    make_work=make_work,
    delivery=delivery,
    retry=retry,
    terminal=lambda: TerminalExample(
        observe=lambda work: PrintOrder.objects.get(pk=work.owner_id).status == "stopped",
        no_rearm_because="A print order is one immutable occurrence; the demo exposes no operator command to authorize another print.",
    ),
    immutable_inputs="A submitted print order is immutable and cannot become a different physical order.",
    applied_receipt=lambda work: provider.submit(str(work.reference), work.title),
    receipt_repair=delivery,
    rejections={"printer refused before acceptance": rejected},
    uncertainties={"accepted but response lost": uncertainty},
    observation=observation,
    admissions={"order print": producer},
    admission_failures={"order print": admission_failure},
)
