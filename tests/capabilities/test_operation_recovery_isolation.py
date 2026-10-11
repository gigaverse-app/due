"""Receipt repair and negative evidence must survive poison without replaying effects."""

from datetime import timedelta

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from due.django.evidence.attempts import attempts_for, observe_effect_outcome, record_effect_attempt
from due.django.models import DurableWorkState, EffectAttemptOutcome, RecoveryDeferral
from due.django.runtime.coordination import claim_work
from due.django.runtime.metrics import RecoveryPhase
from due.django.testing import hold_nudges
from tests.harness.operation import ProviderResult, Publication, PublicationWork, record
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = pytest.mark.django_db(transaction=True)


def arrange(publication: Publication, phase: RecoveryPhase) -> PublicationWork:
    work = record(publication)
    claim = claim_work(type(work), work.pk, lease_duration=publication.policy.lease_duration)
    assert claim is not None
    attempt = record_effect_attempt(claim)
    if phase == RecoveryPhase.EVIDENCE_SETTLEMENT:
        publication.record_applied_effect_evidence(attempt, ProviderResult(value="applied"))
    else:
        PublicationWork.objects.filter(pk=work.pk).update(state=DurableWorkState.UNKNOWN)
        observe_effect_outcome(attempt, EffectAttemptOutcome.NOT_APPLIED)
    return work


def progressed(work: PublicationWork, phase: RecoveryPhase) -> bool:
    work.refresh_from_db()
    if phase == RecoveryPhase.EVIDENCE_SETTLEMENT:
        return (
            PrimitiveOwner.objects.get(pk=work.owner_id).value == "applied"
            and not attempts_for(work).get().repair_pending
        )
    return work.state in (DurableWorkState.RETRYABLE, DurableWorkState.QUEUED)


@pytest.mark.parametrize("phase", [RecoveryPhase.EVIDENCE_SETTLEMENT, RecoveryPhase.NONAPPLICATION])
@pytest.mark.parametrize("fault", [None, RuntimeError, SoftTimeLimitExceeded], ids=["control", "poison", "stop"])
def test_recovery_preserves_owed_evidence_and_isolates_only_identity_failures(
    publication: Publication, monkeypatch: pytest.MonkeyPatch, phase: RecoveryPhase, fault: type[Exception] | None
) -> None:
    bad, good = arrange(publication, phase), arrange(publication, phase)
    calls: list[int] = []

    def fail(work: PublicationWork) -> None:
        calls.append(work.pk)
        if work.pk == bad.pk and fault is not None:
            raise fault("injected per-identity failure")

    if phase == RecoveryPhase.EVIDENCE_SETTLEMENT:
        original_finalize = Publication.finalize_applied

        def finalize(self: Publication, work: PublicationWork, result: ProviderResult) -> None:
            fail(work)
            original_finalize(self, work, result)

        monkeypatch.setattr(Publication, "finalize_applied", finalize)
    else:
        original_delay = Publication.retry_delay_for

        def delay(self: Publication, work: PublicationWork) -> timedelta:
            fail(work)
            return original_delay(self, work)

        monkeypatch.setattr(Publication, "retry_delay_for", delay)

    with hold_nudges():
        if fault is SoftTimeLimitExceeded:
            with pytest.raises(SoftTimeLimitExceeded):
                publication.recover()
            assert calls == [bad.pk]
            assert not progressed(good, phase)
            assert not RecoveryDeferral.objects.exists()
        elif fault is RuntimeError:
            with pytest.raises(ExceptionGroup) as caught:
                publication.recover()
            assert caught.group_contains(RuntimeError, match="injected per-identity failure")
            assert calls == [bad.pk, good.pk]
            assert progressed(good, phase)
            assert not progressed(bad, phase)
            assert RecoveryDeferral.objects.filter(phase=phase, identity=str(bad.pk)).exists()
            calls.clear()
            publication.recover()
            assert bad.pk not in calls  # Poison is held out on the following tick.
        else:
            publication.recover()
            assert progressed(bad, phase) and progressed(good, phase)
    assert Publication.calls == []  # Evidence repair never resends the external operation.
    if fault is not None and phase == RecoveryPhase.EVIDENCE_SETTLEMENT:
        assert attempts_for(bad).get().repair_pending
