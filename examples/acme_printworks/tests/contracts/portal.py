"""The portal contract binds the actual proof-settlement producer."""

from due.django.conformance.composition import ProjectionExample
from due.django.conformance.seams import ProviderSeam, seam_examples
from due.django.conformance.suite import TerminalExample
from pytest_obligation.contract import NotApplicable
from pytest_obligation.profiles.catalog import ConvergenceFamily

from acme.effects import project_portal, publish_proof
from acme.models import Job
from acme.services import add_revision, request_proof, retry_portal

from .support import COPY, NEW_COPY, environment, make_portal, portal_contents


def snapshot():
    work = make_portal()

    def change():
        revision = add_revision(work.owner_id, 19, NEW_COPY)
        newer = request_proof(revision.pk)
        publish_proof.attempt_execution(newer.pk)

    return ProjectionExample(work=work, change=change, observe=lambda: portal_contents(work), latest=NEW_COPY)


# ARRANGE: a real proof settlement admits its portal follow-up.
# REAL PRODUCTION: PortalProjection writes and repairs the hosted approval view.
# EXTERNAL SEAM: count/refuse unconditional independently committed portal writes.
# OBSERVE: read the proof text actually displayed by the portal.
SEAM = ProviderSeam(
    environment=environment,
    make_work=make_portal,
    observe=lambda provider, work: portal_contents(work),
    delivered=lambda provider, work: COPY,
    calls=lambda provider: provider.portal.total,
    calls_for=lambda provider, work: provider.portal.count(work.owner_id),
    refuse=lambda provider: provider.portal.reject(lambda: OSError("Portal network unavailable")),
)

EXAMPLES = seam_examples(
    project_portal,
    SEAM,
    environment=environment,
    terminal=lambda: TerminalExample(
        observe=lambda work: Job.objects.get(pk=work.owner_id).portal_failed,
        rearm=lambda work: retry_portal(work.owner_id),
    ),
    snapshot=snapshot,
    admission_not_applicable="The portal work row is admitted inside the proof's fenced settlement transaction; proof producer and handoff histories exercise that transaction.",
    convergence_families={
        family: NotApplicable(
            "The portal overwrites one proof pointer; stale snapshots and result application own its convergence, without merging independent records or dependency gates."
        )
        for family in ConvergenceFamily
        if family.name not in {"STALE_SNAPSHOTS", "MONOTONIC_RESULTS"}
    },
)
