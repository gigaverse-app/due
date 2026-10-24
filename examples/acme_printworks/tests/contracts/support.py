"""Count real provider calls; contract bindings never implement Due transitions."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from due.django.conformance.provider import ProviderProbe
from due.django.testing import hold_nudges

from acme import providers
from acme.effects import publish_proof
from acme.models import Job, PortalWork
from acme.services import add_revision, request_proof

COPY = "New opening day: June 19"
NEW_COPY = "Opening day: June 26"


@dataclass
class Calls:
    render: ProviderProbe
    portal: ProviderProbe
    submit: ProviderProbe
    observe: ProviderProbe
    cleanup: ProviderProbe


calls: ContextVar[Calls] = ContextVar("acme_provider_calls")


@contextmanager
def environment():
    # Nested bindings share their counters so outer proof controls see real I/O.
    current = calls.get(None)
    if current is not None:
        yield current
        return
    observed = Calls(
        ProviderProbe(providers.render, key=lambda text, key, spool: key),
        ProviderProbe(providers.put_portal, key=lambda job, key: job),
        ProviderProbe(providers.submit_print, key=lambda reference, key, quantity: reference),
        ProviderProbe(providers.observe_print, key=lambda reference: reference),
        ProviderProbe(providers.cleanup, key=lambda spool: spool),
    )
    token = calls.set(observed)
    try:
        with (
            observed.render.patch(providers, "render"),
            observed.portal.patch(providers, "put_portal"),
            observed.submit.patch(providers, "submit_print"),
            observed.observe.patch(providers, "observe_print"),
            observed.cleanup.patch(providers, "cleanup"),
        ):
            yield observed
    finally:
        calls.reset(token)


def new_revision(text=COPY):
    with hold_nudges():
        return add_revision(Job.objects.create().pk, 18, text)


def make_proof():
    with hold_nudges():
        return request_proof(new_revision().pk)


def published_job():
    work = make_proof()
    with hold_nudges():
        publish_proof.attempt_execution(work.pk)
    return Job.objects.get(pk=work.owner_id)


def make_portal():
    return PortalWork.objects.get(owner=published_job())


def proof_contents(work):
    job = Job.objects.get(pk=work.owner_id)
    return providers.proof_text(job.published_key) if job.published_key else None


def portal_contents(work):
    key = providers.portal_key(work.owner_id)
    return providers.proof_text(key) if key else None
