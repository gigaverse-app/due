"""Unsolicited receipts require an existing, unambiguous provider crossing."""

import pytest

from due.django.evidence.attempts import attempts_for, record_effect_attempt
from due.django.models import EffectAttempt, EffectAttemptOutcome
from due.django.runtime.coordination import claim_work
from due.errors import EffectNotAppliedError
from tests.harness.operation import (
    ProviderResult,
    Publication,
    Receipt,
    record,
)
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_observation_before_provider_entry_cannot_invent_an_attempt(publication: Publication) -> None:
    work = record(publication)
    assert not publication.record_observed_effect_evidence(work.pk, ProviderResult(value="applied"))
    assert publication.possible_effect_attempt(work) is None
    assert not attempts_for(work).exists()
    assert not Receipt.objects.exists()


@pytest.mark.parametrize("already_applied", [False, True])
def test_observation_attaches_to_inflight_or_receipted_crossing(
    publication: Publication, already_applied: bool
) -> None:
    work = record(publication)
    other = record(publication)
    claim = claim_work(type(work), work.pk, lease_duration=publication.policy.lease_duration)
    assert claim is not None
    attempt = record_effect_attempt(claim)
    entry = publication.possible_effect_attempt(work)
    assert entry is not None
    assert (entry.pk, entry.started_at) == (attempt.pk, attempt.started_at)
    assert publication.possible_effect_attempt(other) is None
    result = ProviderResult(value="applied")
    if already_applied:
        publication.record_applied_effect_evidence(attempt, result)
    assert publication.record_observed_effect_evidence(work.pk, result)
    assert publication.settle_from_applied_effect_evidence(work.pk)
    assert attempts_for(work).get().outcome == EffectAttemptOutcome.APPLIED
    settled = publication.possible_effect_attempt(work)
    assert settled is not None
    assert settled.pk == attempt.pk  # Settlement never forgets that this occurrence entered.
    assert not attempts_for(other).exists()
    assert Publication.calls == []


def test_observation_cannot_reuse_a_rejected_crossing(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = record(publication)
    monkeypatch.setattr(Publication, "failure", EffectNotAppliedError("rejected"))
    with pytest.raises(EffectNotAppliedError):
        publication.attempt_execution(work.pk)
    assert not publication.record_observed_effect_evidence(work.pk, ProviderResult(value="applied"))
    assert attempts_for(work).get().outcome == EffectAttemptOutcome.NOT_APPLIED
    assert publication.possible_effect_attempt(work) is None
    assert not Receipt.objects.exists()


def test_possible_entry_refuses_ambiguity_instead_of_picking_latest(publication: Publication) -> None:
    work = record(publication)
    claim = claim_work(type(work), work.pk, lease_duration=publication.policy.lease_duration)
    assert claim is not None
    attempt = record_effect_attempt(claim)
    # Deliberately corrupt root-owned evidence to exercise the fail-closed read.
    EffectAttempt.objects.create(
        work_model=attempt.work_model,
        work_id=attempt.work_id,
        claim_token=attempt.claim_token,
        desired_revision=attempt.desired_revision,
        started_at=attempt.started_at,
    )
    with pytest.raises(EffectAttempt.MultipleObjectsReturned):
        publication.possible_effect_attempt(work)
