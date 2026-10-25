"""Provider waits must also survive a bulk adopter's inline retry preference."""

from contextlib import suppress
from datetime import timedelta

import pytest

from due.bulk import RejectionResponse
from due.django.models import DurableWorkState
from due.django.runtime.coordination import ContinueAfter, database_now
from due.errors import EffectNotAppliedError, EffectRejection
from tests.harness.bulk import BatchOperation, record
from tests.harness.bulk import batch_environment as batch_environment
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.replay_safe_bulk import MemberSync, record_sync
from tests.harness.replay_safe_bulk import member_sync_environment as member_sync_environment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("fixture_name", ["batch_operation", "member_sync_operation"])
@pytest.mark.parametrize(
    "preference",
    [RejectionResponse.RETRY_INLINE, ContinueAfter(timedelta(seconds=1), until=None)],
)
def test_provider_floor_defers_bulk_reentry(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    fixture_name: str,
    preference: RejectionResponse | ContinueAfter,
) -> None:
    operation = request.getfixturevalue(fixture_name)
    provider = BatchOperation if fixture_name == "batch_operation" else MemberSync
    work = record(operation) if fixture_name == "batch_operation" else record_sync()
    provider.script.append(EffectNotAppliedError(EffectRejection(message="429", retry_after=timedelta(hours=1))))
    monkeypatch.setattr(provider, "on_batch_rejected", lambda self, work, rejection: preference)
    started = database_now()
    with suppress(EffectNotAppliedError):
        operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert provider.calls == [("a", "b", "c")]
    assert work.available_at >= started + timedelta(hours=1)
    assert work.state == DurableWorkState.RETRYABLE
    assert work.rearm_count == 1
    assert provider.calls == [("a", "b", "c")]
    assert operation.attempt_execution(work.pk) is False
    assert provider.calls == [("a", "b", "c")]
    work.refresh_from_db()
    assert work.rearm_count == 1
