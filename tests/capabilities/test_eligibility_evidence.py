"""Execution eligibility never suppresses provider evidence reconciliation."""

from dataclasses import replace

import pytest
from django.db.models import Q

from due.django.evidence.attempts import attempts_for, observe_effect_outcome
from due.django.models import EffectAttempt, EffectAttemptOutcome
from due.django.public import DurableWorkState, ExecutionEligibility
from due.django.testing import WorkerDied, expire_lease
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
