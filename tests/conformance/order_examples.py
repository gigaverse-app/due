"""Printer contracts count actual requests; provider controls never settle Due work."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from unittest.mock import patch

from due.django.conformance.observations import ObservationExample
from due.django.conformance.provider import ProviderProbe
from due.django.conformance.seams import ProviderSeam, seam_examples
from due.django.conformance.suite import (
    AdmissionFailureExample,
    ProducerExample,
    TerminalExample,
)
from due.django.testing import hold_nudges
from due.errors import WorkFailureDisposition
from examples.orders import provider
from examples.orders.effects import print_order
from examples.orders.models import PrintOrder, PrintWork
from examples.orders.provider import PrintReceipt
from examples.orders.services import order_print, receive_printer_receipt
from tests.examples.books_support import index_environment


@dataclass
class PrinterCalls:
    submits: ProviderProbe[[str, str], PrintReceipt, str]
    reads: ProviderProbe[[str], PrintReceipt | None, str]


calls: ContextVar[PrinterCalls] = ContextVar("printer_calls")


@contextmanager
def environment() -> Iterator[PrinterCalls]:
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
            yield observed
    finally:
        calls.reset(token)


def make_work() -> PrintWork:
    with hold_nudges():
        return order_print("A physical book")


# ARRANGE: order a physical book through the application command.
# REAL PRODUCTION: submit once, settle a receipt, or observe an accepted unknown occurrence.
# EXTERNAL SEAM: real SQLite printer ledger with refusal/response-loss scopes.
# OBSERVE: persisted order status and independently counted submission calls.
SEAM = ProviderSeam(
    environment=environment,
    make_work=make_work,
    observe=lambda printer, work: PrintOrder.objects.get(pk=work.owner_id).status,
    delivered=lambda printer, work: "printed",
    calls=lambda printer: printer.submits.total,
    calls_for=lambda printer, work: printer.submits.count(str(work.reference)),
    refuse=lambda printer: printer.submits.reject(
        lambda: provider.PrinterRejected("Printer refused before acceptance")
    ),
)
delivery = SEAM.delivery
rejected = SEAM.rejection(
    lambda printer, work: printer.submits.reject(lambda: provider.PrinterRejected("Printer refused before acceptance")),
    lambda printer, work: "retrying",
    WorkFailureDisposition.RETRYABLE,
)


@contextmanager
def lost_response(printer: PrinterCalls) -> Iterator[None]:
    with (
        printer.submits.lose_response(lambda: TimeoutError("Printer response lost after acceptance")),
        patch.dict(os.environ, {"DUE_PRINTER_HIDE_RECEIPTS": "1"}),
    ):
        yield


uncertainty = SEAM.outcome(lambda printer, work: lost_response(printer), lambda printer, work: "unknown")


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


EXAMPLES = seam_examples(
    print_order,
    SEAM,
    fixtures=("printer",),
    environment=environment,
    terminal=lambda: TerminalExample(
        observe=lambda work: PrintOrder.objects.get(pk=work.owner_id).status == "stopped",
        no_rearm_because="A print order is one immutable occurrence; the demo exposes no operator command to authorize another print.",
    ),
    immutable_inputs="A submitted print order is immutable and cannot become a different physical order.",
    applied_receipt=lambda work: provider.submit(str(work.reference), work.title),
    # REAL PRODUCTION: the printer callback; generated ambiguity proofs settle through it.
    evidence_entry=lambda work, receipt: receive_printer_receipt(receipt),
    receipt_repair=delivery,
    rejections={"printer refused before acceptance": rejected},
    uncertainties={"accepted but response lost": uncertainty},
    observation=observation,
    admissions={"order print": producer},
    admission_failures={"order print": admission_failure},
)
