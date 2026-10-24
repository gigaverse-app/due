"""Acme supplies product meaning; Due generates the failure histories."""

from contextlib import contextmanager

from due.django.conformance.composition import CompensationExample, SupersessionExample
from due.django.conformance.seams import ProviderSeam, seam_examples
from due.django.conformance.suite import AdmissionFailureExample, ProducerExample, TerminalExample
from pytest_obligation.contract import NotApplicable
from pytest_obligation.profiles.catalog import ConvergenceFamily

from acme import providers
from acme.effects import publish_proof, render_proof
from acme.models import CleanupWork, Job, ProofWork
from acme.services import add_revision, request_proof, retry_proof

from .support import COPY, NEW_COPY, calls, environment, make_proof, new_revision, proof_contents


def provider_contents(work):
    with providers.database() as db:
        row = db.execute("SELECT text FROM proofs WHERE key=?", (work.provider_idempotency_key(),)).fetchone()
        return row[0] if row else None


def snapshot():
    work = make_proof()
    return SupersessionExample(
        work=work,
        change=lambda: request_proof(add_revision(work.owner_id, 19, NEW_COPY).pk),
        observe=lambda: proof_contents(work),
    )


@contextmanager
def producer():
    with environment():
        revision = new_revision()
        yield ProducerExample(
            produce=lambda: (request_proof(revision.pk),),
            observe=lambda: (
                providers.proof_text(Job.objects.get(pk=revision.job_id).published_key)
                if Job.objects.get(pk=revision.job_id).published_key
                else None
            ),
            expected=COPY,
            effect_calls=lambda: calls.get().render.total,
        )


@contextmanager
def admission_failure():
    with environment():
        revision = new_revision()
        yield AdmissionFailureExample.after_save(
            ProducerExample(
                produce=lambda: (request_proof(revision.pk),),
                observe=lambda: Job.objects.get(pk=revision.job_id).current_revision_id,
                expected=revision.pk,
                effect_calls=lambda: calls.get().render.total,
            ),
            ProofWork,
        )


def compensation():
    work = make_proof()
    attempt = []

    def late_output(current):
        if not attempt:
            attempt.append(CleanupWork.objects.latest("pk").spool_key)
        providers.put_spool(attempt[0], current.text, finished=False)

    def remaining():
        with providers.database() as db:
            return db.execute("SELECT count(*) FROM renders WHERE spool IS NOT NULL").fetchone()[0]

    return CompensationExample(
        work=work,
        provider_path="acme.effects.render_proof",
        produce_output=late_output,
        # Scratch data is always disposable; no product-retirement gate is needed.
        retire=lambda: (),
        observe=remaining,
        clean=0,
    )


# ARRANGE: request a proof through Acme's production command.
# REAL PRODUCTION: immutable rendering and fenced publication of the job's pointer.
# EXTERNAL SEAM: count/refuse real renderer calls with independent provider commits.
# OBSERVE: read customer-visible proof bytes; replay separately inspects provider bytes.
SEAM = ProviderSeam(
    environment=environment,
    make_work=make_proof,
    observe=lambda provider, work: proof_contents(work),
    delivered=lambda provider, work: COPY,
    provider_state=lambda provider, work: provider_contents(work),
    provider_expected=lambda provider, work: COPY,
    calls=lambda provider: provider.render.total,
    calls_for=lambda provider, work: provider.render.count(work.provider_idempotency_key()),
    refuse=lambda provider: provider.render.reject(lambda: OSError("Renderer power supply unavailable")),
)

EXAMPLES = seam_examples(
    publish_proof,
    SEAM,
    replay=SEAM.replay("Acme immutable rendering", render_proof),
    environment=environment,
    terminal=lambda: TerminalExample(
        observe=lambda work: Job.objects.get(pk=work.owner_id).proof_failed,
        rearm=lambda work: retry_proof(work.owner_id),
    ),
    snapshot=snapshot,
    compensation=compensation,
    producers={"request proof": producer},
    admission_failures={"partial proof request": admission_failure},
    convergence_families={
        family: NotApplicable(
            "Immutable proof keys keep old bytes separate; the current revision's fenced pointer is the only mutable result. There is no merge or dependency gate."
        )
        for family in ConvergenceFamily
        if family.name != "STALE_SNAPSHOTS"
    },
)
