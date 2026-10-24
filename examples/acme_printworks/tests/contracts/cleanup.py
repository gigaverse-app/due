"""Cleanup stays owed after its producer dies, even when deletion is inconclusive."""

from uuid import uuid4

from due.django.conformance.seams import ProviderSeam, seam_examples

from acme import providers
from acme.effects import cleanup_spool
from acme.models import CleanupWork

from .support import environment


def make_work():
    # Baseline owed-row fixture for the generic lifecycle profiles. Producer
    # admission is proved separately by proof.compensation() and process death.
    # Do not recover here: setup must not dispatch other histories' work.
    key = str(uuid4())
    providers.put_spool(key, "Disposable renderer bytes", finished=True)
    return CleanupWork.objects.create(spool_key=key, universe="acme")


def remaining(work):
    with providers.database() as db:
        return db.execute("SELECT spool FROM renders WHERE key=?", (work.spool_key,)).fetchone()[0]


# ARRANGE: an owed cleanup row and independently committed disposable bytes.
# REAL PRODUCTION: SpoolCleanup deletes temporary output and checks terminal completion.
# EXTERNAL SEAM: count/refuse the provider's actual cleanup transaction.
# OBSERVE: independently stored spool bytes, rather than the cleanup row status.
SEAM = ProviderSeam(
    environment=environment,
    make_work=make_work,
    observe=lambda provider, work: remaining(work),
    delivered=lambda provider, work: None,
    calls=lambda provider: provider.cleanup.total,
    calls_for=lambda provider, work: provider.cleanup.count(work.spool_key),
    refuse=lambda provider: provider.cleanup.reject(lambda: OSError("Temporary storage disconnected")),
)

EXAMPLES = seam_examples(
    cleanup_spool,
    SEAM,
    environment=environment,
    replay=SEAM.replay("Acme temporary spool deletion", cleanup_spool.compensate),
    immutable_inputs="Each cleanup captures one producer attempt's spool key; it cannot be reassigned to a different attempt and has no mutable product input.",
    admission_not_applicable="Cleanup admission is one reserved work-row insert in record_cleanup_before_call, exercised through the producer's compensation histories.",
)
