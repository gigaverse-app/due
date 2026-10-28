"""A composed entry commits authority and uncertainty before real external I/O."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, get_ident
from unittest.mock import patch

import pytest
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test.utils import CaptureQueriesContext

from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState, EffectAttemptOutcome
from due.django.runtime.coordination import ContinueAfter, claim_work
from due.django.testing import expire_lease, make_due
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import SendChunk
from tests.harness.self_work import chunk_environment as chunk_environment
from tests.harness.self_work import inline_chunk_environment as inline_chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("fixture", ["self_chunk", "inline_chunk"])
@pytest.mark.parametrize("enabled", [False, True])
def test_claim_and_entry_share_one_commit_without_changing_delivery(request, monkeypatch, fixture, enabled):
    capability = request.getfixturevalue(fixture)
    monkeypatch.setattr(type(capability), "combine_claim_and_entry", enabled, raising=False)
    work = capability.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    make_due(work)
    original = SendChunk.send_batch

    def send(instance, current, batch, payload):
        assert connection.get_autocommit() and not connection.in_atomic_block
        attempt = attempts_for(current).get()
        assert attempt.outcome == EffectAttemptOutcome.UNKNOWN
        assert attempt.claim_token == current.fencing_token
        return original(instance, current, batch, payload)

    with patch.object(SendChunk, "send_batch", send), CaptureQueriesContext(connection) as queries:
        assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.delivered == ["a", "b"]
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]
    assert len(SendChunk.calls) == 1
    assert sum(query["sql"] == "COMMIT" for query in queries) == (3 if enabled else 4)


@pytest.mark.parametrize("fixture", ["self_chunk", "inline_chunk"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("database_error", [False, True])
def test_failed_started_hook_preserves_retry_budget_and_rolls_back_attempt(
    request, monkeypatch, fixture, enabled, database_error
):
    capability = request.getfixturevalue(fixture)
    monkeypatch.setattr(type(capability), "combine_claim_and_entry", enabled, raising=False)
    work = capability.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    make_due(work)
    reached = []

    def fail(instance, current):
        reached.append(current.pk)
        current.product_status = "uncommitted"
        current.save(update_fields=["product_status"])
        if database_error:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 / 0")
        raise RuntimeError("started hook failed")

    from django.db import DatabaseError

    with (
        patch.object(SendChunk, "finalize_started", fail),
        pytest.raises(DatabaseError if database_error else RuntimeError),
    ):
        capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert reached == [work.pk]
    assert work.state == DurableWorkState.RETRYABLE and work.rearm_count == 1 and work.attempt_count == 1
    assert work.product_status == "retry"
    assert not attempts_for(work).exists()
    assert not SendChunk.calls
    make_due(work)
    assert capability.attempt_execution(work.pk)
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]


@pytest.mark.parametrize("fixture", ["self_chunk", "inline_chunk"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("queued", [False, True])
def test_entry_is_durable_and_locks_are_released_before_provider(request, monkeypatch, fixture, enabled, queued):
    capability = request.getfixturevalue(fixture)
    monkeypatch.setattr(type(capability), "combine_claim_and_entry", enabled, raising=False)
    work = capability.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    make_due(work)
    reservation = (
        claim_work(capability.model, work.pk, lease_duration=capability.policy.lease_duration, queued=True)
        if queued
        else None
    )
    token = reservation.token if reservation is not None else None
    original = SendChunk.send_batch

    def independently_visible():
        close_old_connections()
        try:
            with transaction.atomic():
                current = capability.model.objects.select_for_update(nowait=True).get(pk=work.pk)
                attempt = attempts_for(current).get()
                return current.state, attempt.outcome, attempt.claim_token == current.fencing_token
        finally:
            connections.close_all()

    def send(instance, current, batch, payload):
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(independently_visible).result(timeout=5) == (
                DurableWorkState.CLAIMED,
                EffectAttemptOutcome.UNKNOWN,
                True,
            )
        return original(instance, current, batch, payload)

    with patch.object(SendChunk, "send_batch", send):
        assert capability.attempt_execution(work.pk, token)
        assert not capability.attempt_execution(work.pk, token)
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]
    assert len(SendChunk.calls) == 1


@pytest.mark.parametrize("fixture", ["self_chunk", "inline_chunk"])
@pytest.mark.parametrize("enabled", [False, True])
def test_pre_entry_pause_retains_intent_without_attempt_or_budget(request, monkeypatch, fixture, enabled):
    capability = request.getfixturevalue(fixture)
    monkeypatch.setattr(type(capability), "combine_claim_and_entry", enabled, raising=False)
    work = capability.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    make_due(work)
    with patch.object(SendChunk, "before_batch", return_value=ContinueAfter(timedelta(seconds=1), until=None)):
        assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY and work.rearm_count == 0
    assert not attempts_for(work).exists() and not SendChunk.calls
    make_due(work)
    assert capability.attempt_execution(work.pk)
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]


@pytest.mark.parametrize("fixture", ["self_chunk", "inline_chunk"])
def test_failed_entry_commit_never_calls_provider_and_uses_normal_retry(request, monkeypatch, fixture):
    capability = request.getfixturevalue(fixture)
    monkeypatch.setattr(type(capability), "combine_claim_and_entry", True, raising=False)
    work = capability.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    make_due(work)
    original = connection.commit
    failures = []

    def fail_once():
        if not failures:
            failures.append(True)
            raise IntegrityError("entry commit refused")
        original()

    with patch.object(connection, "commit", fail_once), pytest.raises(IntegrityError, match="entry commit refused"):
        capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert failures == [True] and work.state == DurableWorkState.RETRYABLE and work.rearm_count == 1
    assert not attempts_for(work).exists() and not SendChunk.calls
    make_due(work)
    assert capability.attempt_execution(work.pk)
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]


@pytest.mark.parametrize("fixture", ["self_chunk", "inline_chunk"])
def test_post_commit_callback_failure_retains_uncertainty_without_resend(request, monkeypatch, fixture):
    capability = request.getfixturevalue(fixture)
    monkeypatch.setattr(type(capability), "combine_claim_and_entry", True)
    work = capability.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    make_due(work)

    def fail_after_commit():
        raise RuntimeError("entry committed but callback failed")

    def register_callback(instance, current):
        transaction.on_commit(fail_after_commit)

    with (
        patch.object(SendChunk, "finalize_started", register_callback),
        pytest.raises(RuntimeError, match="entry committed but callback failed"),
    ):
        capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.CLAIMED
    assert attempts_for(work).get().outcome == EffectAttemptOutcome.UNKNOWN
    assert not SendChunk.calls
    expire_lease(work)
    capability.recover()
    capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.UNKNOWN
    assert not SendChunk.calls and not SendChunk.accepted_by_work.get(work.pk)


@pytest.mark.parametrize("queued", [False, True])
def test_failed_preparation_does_not_settle_a_competing_executor(self_chunk, monkeypatch, queued):
    capability = self_chunk
    monkeypatch.setattr(type(capability), "combine_claim_and_entry", True, raising=False)
    work = capability.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    make_due(work)
    reservation = (
        claim_work(capability.model, work.pk, lease_duration=capability.policy.lease_duration, queued=True)
        if queued
        else None
    )
    token = reservation.token if reservation is not None else None
    first_thread = get_ident()
    rolled_back, entered, finish = Event(), Event(), Event()
    original_rollback, original_send = connection.rollback, SendChunk.send_batch

    def fail_first(instance, current):
        if get_ident() == first_thread:
            raise RuntimeError("first preparation failed")

    def rollback_then_overlap():
        original_rollback()
        rolled_back.set()
        assert entered.wait(10), "competing executor did not reach its provider"

    def hold_provider(instance, current, batch, payload):
        assert get_ident() != first_thread
        entered.set()
        assert finish.wait(10), "first executor did not finish failure accounting"
        return original_send(instance, current, batch, payload)

    def compete():
        close_old_connections()
        try:
            assert rolled_back.wait(10), "first preparation did not roll back"
            return capability.attempt_execution(work.pk, token)
        finally:
            connections.close_all()

    with (
        patch.object(SendChunk, "finalize_started", fail_first),
        patch.object(SendChunk, "send_batch", hold_provider),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        other = pool.submit(compete)
        try:
            with (
                patch.object(connection, "rollback", rollback_then_overlap),
                pytest.raises(RuntimeError, match="first preparation failed"),
            ):
                capability.attempt_execution(work.pk, token)
            work.refresh_from_db()
            assert work.state == DurableWorkState.CLAIMED and work.rearm_count == 0
            assert attempts_for(work).get().outcome == EffectAttemptOutcome.UNKNOWN
        finally:
            finish.set()
        assert other.result(timeout=10)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED and work.delivered == ["a", "b"]
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b"] and len(SendChunk.calls) == 1
