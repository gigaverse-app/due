"""Printer acceptance, definite refusal, ambiguous response, and observation."""

from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import patch

from due.django.conformance.observations import ObservationExample
from due.django.conformance.seams import ProviderSeam, seam_examples
from due.django.conformance.suite import AdmissionFailureExample, ProducerExample, TerminalExample
from due.django.testing import hold_nudges
from due.errors import WorkFailureDisposition

from acme import providers
from acme.effects import order_printing
from acme.models import PrintOrder, PrintWork
from acme.services import approve_print

from .support import calls, environment, published_job


def make_work():
    with hold_nudges():
        job = published_job()
        return approve_print(job.pk, job.published_key).printwork


# ARRANGE: approve a published proof through Acme's actual command.
# REAL PRODUCTION: submit once, retain acceptance evidence, observe uncertainty.
# EXTERNAL SEAM: real independent printer ledger; refuse before acceptance or lose its reply.
# OBSERVE: customer-visible order status and counted POSTs for that exact purchase.
SEAM = ProviderSeam(
    environment=environment,
    make_work=make_work,
    observe=lambda provider, work: PrintOrder.objects.get(pk=work.owner_id).status,
    delivered=lambda provider, work: "accepted",
    calls=lambda provider: provider.submit.total,
    calls_for=lambda provider, work: provider.submit.count(str(work.reference)),
    refuse=lambda provider: provider.submit.reject(lambda: providers.PrinterRejected("Out of ink; order NOT accepted")),
)

rejected = SEAM.rejection(
    lambda provider, work: provider.submit.reject(lambda: providers.PrinterRejected("Out of ink; order NOT accepted")),
    lambda provider, work: "retrying",
    WorkFailureDisposition.RETRYABLE,
)


@contextmanager
def lost_response(provider):
    with (
        provider.submit.lose_response(lambda: TimeoutError("Printer UPS failed after acceptance; reply lost")),
        patch.object(providers, "receipts_visible", False),
    ):
        yield


uncertain = SEAM.outcome(lambda provider, work: lost_response(provider), lambda provider, work: "unknown")


@contextmanager
def observation():
    with uncertain() as scenario:

        def reveal():
            providers.receipts_visible = True
            return providers.print_receipt(str(scenario.work.reference))

        yield ObservationExample(
            delivery=replace(scenario, expected="accepted"),
            reveal=reveal,
            observation_calls=lambda: calls.get().observe.count(str(scenario.work.reference)),
        )


@contextmanager
def producer():
    with environment():
        job = published_job()

        def produce():
            return (approve_print(job.pk, job.published_key).printwork,)

        yield ProducerExample(
            produce=produce,
            observe=lambda: PrintOrder.objects.filter(revision__job=job).exists(),
            expected=True,
            effect_calls=lambda: calls.get().submit.total,
        )


@contextmanager
def admission_failure():
    with producer() as scenario:
        yield AdmissionFailureExample.after_save(scenario, PrintWork)


EXAMPLES = seam_examples(
    order_printing,
    SEAM,
    environment=environment,
    terminal=lambda: TerminalExample(
        observe=lambda work: PrintOrder.objects.get(pk=work.owner_id).status == "stopped",
        no_rearm_because="An approved print order is one purchase; the sample requires an explicitly approved new order instead of authorizing another send for a stopped occurrence.",
    ),
    immutable_inputs="An approved print order captures the proof, quantity and reference; subsequent brochure edits create different revisions and never alter this purchase.",
    applied_receipt=lambda work: providers.submit_print(str(work.reference), work.proof_key, work.quantity),
    receipt_repair=SEAM.delivery,
    rejections={"out of ink before acceptance": rejected},
    uncertainties={"accepted before power failure": uncertain},
    observation=observation,
    admissions={"customer approval": producer},
    admission_failures={"partial approval": admission_failure},
)
