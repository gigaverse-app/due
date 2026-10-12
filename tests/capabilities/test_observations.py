"""The work table retains uncertainty until an exact observation settles it."""

from datetime import timedelta
from unittest.mock import patch

import pytest

from due.django.models import DurableWorkState
from due.django.runtime.coordination import database_now
from due.django.runtime.tasks import observe_durable_work_task
from tests.harness.observation import ObservationProvider, ObservedPublication, ObservedWork, uncertain_work
from tests.harness.observation import observer as observer
from tests.harness.operation import ProviderResult
from tests.harness.primitive import PrimitiveOwner

pytestmark = pytest.mark.django_db(transaction=True)


def test_lost_observation_recovers_and_exact_evidence_settles_without_resend(observer: ObservedPublication) -> None:
    work = uncertain_work(observer, universe="tenant-42")
    messages: list[tuple[str, int, str]] = []

    def publish(*, args: tuple[str, int, str], **options: object) -> None:
        messages.append(args)

    with patch.object(observe_durable_work_task, "apply_async", publish):
        observer.recover()
        observer.recover()
        assert len(messages) == 1
        assert ObservationProvider.reads == []
        lost = messages[0]
        ObservedWork.objects.filter(pk=work.pk).update(
            observation_claimed_at=database_now() - observer.policy.lease_duration - timedelta(seconds=1)
        )
        observer.recover()
        assert len(messages) == 2
        assert not observe_durable_work_task(*lost)
        ObservationProvider.result = ProviderResult(value="confirmed")
        assert observe_durable_work_task(*messages[1])
        assert not observe_durable_work_task(*messages[1])
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.rearm_count == 0
    assert work.attempt_count == 1
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "confirmed"
    assert ObservationProvider.creates == [work.pk]
    assert ObservationProvider.reads == [(work.pk, "tenant-42")]


@pytest.mark.parametrize("unavailable", [False, True])
def test_inconclusive_observation_never_authorizes_resend(observer: ObservedPublication, unavailable: bool) -> None:
    work = uncertain_work(observer)
    if unavailable:
        ObservationProvider.failure = OSError("provider unavailable")
    with patch.object(observe_durable_work_task, "apply_async") as publish:
        observer.recover()
        assert observe_durable_work_task(*publish.call_args.kwargs["args"])
    work.refresh_from_db()
    assert work.state == DurableWorkState.UNKNOWN
    assert not observer.attempt_execution(work.pk)
    assert ObservationProvider.creates == [work.pk]
