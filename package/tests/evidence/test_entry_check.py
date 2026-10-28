"""Entry samples database time and existing evidence under the same work lock."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from due.django.evidence.attempts import attempts_for, record_locked_effect_attempt
from due.django.models import EffectAttemptOutcome
from due.django.runtime.coordination import WorkOwnershipLost, claim_work
from due.django.testing import make_due, use_database_clock
from due.errors import EffectOutcomeUnknownError
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import inline_chunk_environment as inline_chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


def prepare_claim(capability):
    work = capability.model.objects.create(universe="acme", bulk_keys=["a"])
    make_due(work)
    claim = claim_work(capability.model, work.pk, lease_duration=timedelta(minutes=1))
    assert claim is not None
    return claim


def test_entry_combines_clock_and_existing_evidence_without_an_extra_read(inline_chunk):
    claim = prepare_claim(inline_chunk)
    with transaction.atomic():
        current = claim.owned_work().select_for_update().get()
        with CaptureQueriesContext(connection) as queries:
            attempt = record_locked_effect_attempt(claim, current)
    assert len(queries) == 2  # One clock/evidence read and one UNKNOWN insert.
    assert attempt.outcome == EffectAttemptOutcome.UNKNOWN
    assert attempt.started_at < claim.work.lease_expires_at
    with transaction.atomic(), pytest.raises(EffectOutcomeUnknownError):
        current = claim.owned_work().select_for_update().get()
        record_locked_effect_attempt(claim, current)
    assert attempts_for(claim.work).count() == 1


def test_entry_preserves_controlled_clock_and_refuses_expired_lease(inline_chunk):
    claim = prepare_claim(inline_chunk)
    boundary = claim.work.lease_expires_at
    with use_database_clock(lambda: boundary), transaction.atomic(), pytest.raises(WorkOwnershipLost):
        record_locked_effect_attempt(claim, claim.owned_work().select_for_update().get())
    assert not attempts_for(claim.work).exists()
    earlier = boundary - timedelta(seconds=1)
    with use_database_clock(lambda: earlier), transaction.atomic():
        attempt = record_locked_effect_attempt(claim, claim.owned_work().select_for_update().get())
    assert attempt.started_at == earlier


def test_adopter_evidence_override_remains_authoritative(inline_chunk):
    claim = prepare_claim(inline_chunk)
    with patch.object(type(claim.work), "has_possible_effect", return_value=True) as custom:
        with transaction.atomic(), pytest.raises(EffectOutcomeUnknownError):
            record_locked_effect_attempt(claim, claim.owned_work().select_for_update().get())
    custom.assert_called_once()
    assert not attempts_for(claim.work).exists()
