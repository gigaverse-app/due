"""Execution eligibility never suppresses provider evidence reconciliation."""

from dataclasses import replace

import pytest
from django.db.models import Q

from due.django.conformance.background import BackgroundWork
from due.django.conformance.background import background_work as background_work
from due.django.conformance.background import scheduler_clock as scheduler_clock
from due.django.evidence.attempts import attempts_for, observe_effect_outcome
from due.django.models import EffectAttempt, EffectAttemptOutcome
from due.django.public import DurableWorkState, ExecutionEligibility
from due.django.testing import WorkerDied, expire_lease
from tests.harness.observation import (
    ObservationProvider,
    ObservedPublication,
    ObservedWork,
    registered_observation,
    uncertain_work,
)
from tests.harness.observation import observer as observer
from tests.harness.operation import (
    ProviderResult,
    Publication,
    record,
)
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("observation", ["unknown", "applied", "not_applied"])
def test_blocked_execution_still_reconciles_evidence(
    publication: Publication, monkeypatch: pytest.MonkeyPatch, observation: str
) -> None:
    work = record(publication)

    def lose_receipt(self: Publication, attempt: EffectAttempt, result: ProviderResult) -> None:
        raise WorkerDied("provider crossed; acknowledgement lost")

    with monkeypatch.context() as fault:
        fault.setattr(Publication, "record_applied_effect_evidence", lose_receipt)
        with pytest.raises(WorkerDied):
            publication.attempt_execution(work.pk)
    blocked = replace(publication, eligibility=ExecutionEligibility(lambda now: Q(pk__in=[])))
    expire_lease(work)
    blocked.recover()
    work.refresh_from_db()
    assert work.state == DurableWorkState.UNKNOWN
    assert Publication.calls == ["applied"]
    attempt = attempts_for(work).get()
    if observation == "applied":
        blocked.record_applied_effect_evidence(attempt, ProviderResult(value="applied"))
    elif observation == "not_applied":
        observe_effect_outcome(attempt, EffectAttemptOutcome.NOT_APPLIED)
    blocked.recover()
    work.refresh_from_db()
    expected = {
        "unknown": DurableWorkState.UNKNOWN,
        "applied": DurableWorkState.SUCCEEDED,
        "not_applied": DurableWorkState.RETRYABLE,
    }[observation]
    assert work.state == expected
    assert Publication.calls == ["applied"]
    assert (
        PrimitiveOwner.objects.get(pk=work.owner_id).value
        == {
            "unknown": "unknown",
            "applied": "applied",
            "not_applied": "retry",
        }[observation]
    )


def test_closed_gate_parks_new_effects_but_keeps_unknown_observation_live(
    observer: ObservedPublication, background_work: BackgroundWork
) -> None:
    uncertain = uncertain_work(observer)
    blocked = replace(observer, eligibility=ExecutionEligibility(lambda now: Q(pk__in=[])))
    with registered_observation(blocked):
        with blocked.atomic(PrimitiveOwner.objects.create().pk, notify_worker=False) as pending:
            waiting = pending.request_work(ObservedWork(payload="waiting", universe="acme"))
        background_work.run_recovery("observation_self_test")
        uncertain.refresh_from_db()
        assert uncertain.state == DurableWorkState.UNKNOWN
        assert ObservationProvider.reads == [(uncertain.pk, "acme")]

        # Inconclusive observation does not authorize another create. A later
        # positive observation must still publish despite execution being gated.
        ObservationProvider.result = ProviderResult(value="observed")
        background_work.advance(31)
        background_work.run_recovery("observation_self_test")
        uncertain.refresh_from_db()
        waiting.refresh_from_db()
        assert uncertain.state == DurableWorkState.SUCCEEDED
        assert PrimitiveOwner.objects.get(pk=uncertain.owner_id).value == "observed"
        assert (waiting.state, waiting.attempt_count, waiting.rearm_count) == (DurableWorkState.READY, 0, 0)
        assert ObservationProvider.creates == [uncertain.pk]
        assert ObservationProvider.reads == [(uncertain.pk, "acme"), (uncertain.pk, "acme")]
