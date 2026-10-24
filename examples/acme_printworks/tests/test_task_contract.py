"""Application-specific replay proofs also apply to the initial Django task."""

import pytest
from due.django.conformance.provider import ProviderProbe
from due.django.testing import hold_nudges
from pytest_obligation.profiles.harmless_replay import REPLAY_SAFETY_PROOFS, ReplaySafeEffect

from acme.models import Job, Revision
from acme.services import add_revision
from acme.tasks import count_words


def prepare():
    with hold_nudges():
        return add_revision(Job.objects.create().pk, 17, "Opening day: June 12").pk


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("proof", REPLAY_SAFETY_PROOFS, ids=lambda proof: proof.__name__)
def test_word_count_contract(proof):
    execute = ProviderProbe(count_words.func, key=lambda revision_id: revision_id)
    proof(
        ReplaySafeEffect(
            name="Acme immutable revision word count",
            prepare=prepare,
            execute=execute,
            observe=lambda revision_id: Revision.objects.get(pk=revision_id).word_count,
            execution_count_for=execute.count,
            expected=4,
        )
    )
