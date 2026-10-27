"""Idempotent bulk calls: progress per member, resend on uncertainty, pause on a provider rate limit."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from due.django.capabilities.bulk import BulkRetryNeeded
from due.django.models import DurableWorkState
from due.django.testing import make_due
from due.errors import EffectNotAppliedError, EffectRejection, WorkFailureDisposition
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.replay_safe_bulk import MemberSync, RateLimited, record_sync
from tests.harness.replay_safe_bulk import member_sync_environment as member_sync_environment

pytestmark = pytest.mark.django_db(transaction=True)


def owner_value(work_owner_id: int | None) -> str:
    return PrimitiveOwner.objects.get(pk=work_owner_id).value


def test_one_call_adds_the_whole_cohort(member_sync_operation: MemberSync) -> None:
    work = record_sync()
    with CaptureQueriesContext(connection) as queries:
        assert member_sync_operation.attempt_execution(work.pk)
    assert sum("clock_timestamp()" in query["sql"] for query in queries) == 3
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert MemberSync.calls == [("a", "b", "c")]
    assert owner_value(work.owner_id) == "a,b,c"


def test_a_rejected_user_fails_alone_and_spends_nothing(member_sync_operation: MemberSync) -> None:
    MemberSync.script.append({"b": "reject"})
    work = record_sync()
    assert member_sync_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.rearm_count == 0
    assert MemberSync.calls == [("a", "b", "c")]
    assert MemberSync.members == {"a", "c"}
    assert owner_value(work.owner_id) == "a,c"


def test_an_uncertain_call_is_resent_and_the_provider_converges(member_sync_operation: MemberSync) -> None:
    MemberSync.apply_then_lose_reply = True
    work = record_sync()
    with pytest.raises(TimeoutError):
        member_sync_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    # Replay-safe: no evidence wait, the core retry policy resends.
    assert work.state == DurableWorkState.RETRYABLE
    assert work.rearm_count == 1
    make_due(work)
    assert member_sync_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert MemberSync.calls == [("a", "b", "c"), ("a", "b", "c")]
    assert MemberSync.members == {"a", "b", "c"}


def test_only_members_without_a_verdict_are_resent_after_a_failure(member_sync_operation: MemberSync) -> None:
    MemberSync.script.extend([{"b": "retry", "c": "retry"}, TimeoutError("connection reset")])
    work = record_sync()
    with pytest.raises(TimeoutError):
        member_sync_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert owner_value(work.owner_id) == "a"
    make_due(work)
    assert member_sync_operation.attempt_execution(work.pk)
    assert MemberSync.calls == [("a", "b", "c"), ("b", "c"), ("b", "c")]
    assert owner_value(work.owner_id) == "a,b,c"


def test_a_rate_limit_pauses_until_reset_without_spending_budget(member_sync_operation: MemberSync) -> None:
    MemberSync.script.append(EffectNotAppliedError(RateLimited(message="429", reset_seconds=30)))
    work = record_sync()
    assert member_sync_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY
    assert work.rearm_count == 0
    assert member_sync_operation.attempt_execution(work.pk) is False  # Not due before the reset.
    make_due(work)
    assert member_sync_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert MemberSync.calls == [("a", "b", "c"), ("a", "b", "c")]


def test_a_permanent_whole_call_rejection_is_terminal(member_sync_operation: MemberSync) -> None:
    rejection = EffectRejection(message="channel deleted", disposition=WorkFailureDisposition.TERMINAL)
    MemberSync.script.append(EffectNotAppliedError(rejection))
    work = record_sync()
    with pytest.raises(EffectNotAppliedError):
        member_sync_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.TERMINAL_FAILED
    assert MemberSync.members == set()


def test_inline_rounds_hand_remaining_members_to_the_retry_budget(member_sync_operation: MemberSync) -> None:
    MemberSync.script.extend([{"c": "retry"}] * 3)
    work = record_sync()
    with pytest.raises(BulkRetryNeeded):
        member_sync_operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.RETRYABLE
    assert MemberSync.calls == [("a", "b", "c"), ("c",), ("c",)]
    assert owner_value(work.owner_id) == "a,b"
