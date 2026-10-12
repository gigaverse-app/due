"""
Independent native-lease histories: real PostgreSQL, only the clock is controlled.

These exercise the shared primitive independently of application adoption. Failure
timing, takeover and publication rollback must compose with retained claims.
"""

import asyncio
from contextlib import nullcontext
from datetime import timedelta

import pytest
from asgiref.sync import AsyncSingleThreadContext, async_to_sync

from due.django.completion import CompletionLease
from due.django.conformance.background import frozen_scheduler_clock
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

LEASE = timedelta(minutes=5)
EPSILON = timedelta(microseconds=1)
pytestmark = pytest.mark.usefixtures("isolated_primitive")


def require_ready(row: PrimitiveOwner) -> None:
    if row.value != "ready":
        raise ValueError("publication requires ready product state")


completion = CompletionLease(
    model=PrimitiveOwner,
    claimed_at="claimed_at",
    completed_at="completed_at",
    validate=require_ready,
)


@pytest.mark.parametrize("failure_offset", [-EPSILON, timedelta(0), EPSILON], ids=["before", "at", "after"])
def test_failure_never_moves_the_original_expiry(failure_offset: timedelta) -> None:
    owner = PrimitiveOwner.objects.create(value="ready")
    with frozen_scheduler_clock() as clock:
        with pytest.raises(RuntimeError, match="provider response failed"):
            with completion.held(owner.pk, lease_duration=LEASE, retain_on_failure=True) as token:
                assert token is not None
                deadline = token + LEASE
                clock.move_to(deadline + failure_offset)
                raise RuntimeError("provider response failed")

        owner.refresh_from_db()
        assert owner.claimed_at == token
        assert owner.completed_at is None
        if failure_offset < timedelta(0):
            assert not completion.available(PrimitiveOwner.objects.all(), lease_duration=LEASE).exists()
            assert completion.claim(owner.pk, lease_duration=LEASE) is None
            clock.move_to(deadline)

        # Inclusive boundary, with a non-default lease: failure did not add a delay.
        assert list(completion.available(PrimitiveOwner.objects.all(), lease_duration=LEASE)) == [owner]
        successor = completion.claim(owner.pk, lease_duration=LEASE)
        assert successor is not None and successor != token
        assert completion.complete(owner.pk, token=successor)


@pytest.mark.parametrize("retain", [False, True], ids=["default", "retained"])
@pytest.mark.parametrize("exit_kind", ["normal", "failure", "cancellation"])
def test_old_scope_exit_cannot_release_or_complete_a_successor(retain: bool, exit_kind: str) -> None:
    owner = PrimitiveOwner.objects.create(value="ready")
    error = asyncio.CancelledError() if exit_kind == "cancellation" else RuntimeError("old call failed")
    expected = nullcontext() if exit_kind == "normal" else pytest.raises(type(error))
    with frozen_scheduler_clock() as clock, expected:
        with completion.held(owner.pk, lease_duration=LEASE, retain_on_failure=retain) as old:
            assert old is not None
            clock.move_to(old + LEASE)
            current = completion.claim(owner.pk, lease_duration=LEASE)
            assert current is not None and current != old
            assert not completion.complete(owner.pk, token=old)
            if exit_kind != "normal":
                raise error

    owner.refresh_from_db()
    assert owner.claimed_at == current
    assert owner.completed_at is None
    # Positive control: the successor, not merely nobody, can publish and settle.
    assert completion.complete(owner.pk, token=current)
    owner.refresh_from_db()
    assert owner.completed_at is not None
    assert owner.claimed_at is None


def test_failed_finalizer_rolls_back_product_writes_but_retains_completion_authority() -> None:
    owner = PrimitiveOwner.objects.create(value="ready")

    def failing_publication(row: PrimitiveOwner) -> None:
        row.value = "published"
        row.save(update_fields=["value"])
        raise RuntimeError("publication transaction failed")

    def publication(row: PrimitiveOwner) -> None:
        row.value = "published"
        row.save(update_fields=["value"])

    with frozen_scheduler_clock() as clock:
        with pytest.raises(RuntimeError, match="publication transaction failed"):
            with completion.held(owner.pk, lease_duration=LEASE, retain_on_failure=True) as token:
                assert token is not None
                completion.complete(owner.pk, token=token, finalize=failing_publication)
        owner.refresh_from_db()
        assert owner.value == "ready"
        assert owner.completed_at is None
        assert owner.claimed_at == token
        assert completion.claim(owner.pk, lease_duration=LEASE) is None

        clock.move_to(token + LEASE)
        with completion.held(owner.pk, lease_duration=LEASE, retain_on_failure=True) as current:
            assert current is not None
            assert completion.complete(owner.pk, token=current, finalize=publication)
        owner.refresh_from_db()
        assert owner.value == "published"
        assert owner.completed_at is not None
        assert owner.claimed_at is None


def test_failure_after_receipt_commit_cannot_reopen_completion() -> None:
    owner = PrimitiveOwner.objects.create(value="ready")
    with pytest.raises(RuntimeError, match="post-completion observer failed"):
        with completion.held(owner.pk, lease_duration=LEASE, retain_on_failure=True) as token:
            assert token is not None
            assert completion.complete(owner.pk, token=token)
            raise RuntimeError("post-completion observer failed")
    owner.refresh_from_db()
    assert owner.claimed_at is None
    assert owner.completed_at is not None
    assert completion.claim(owner.pk, lease_duration=LEASE) is None
    assert not completion.available(PrimitiveOwner.objects.all(), lease_duration=LEASE).exists()


def test_availability_preserves_caller_scope_and_does_not_replace_product_validation() -> None:
    owed = PrimitiveOwner.objects.create(value="ready")
    unrelated = PrimitiveOwner.objects.create(value="ready")
    invalid = PrimitiveOwner.objects.create(value="not ready")
    selection = PrimitiveOwner.objects.filter(pk__in=[owed.pk, invalid.pk]).order_by("-pk")
    assert list(completion.available(selection, lease_duration=LEASE)) == [invalid, owed]
    assert unrelated not in completion.available(selection, lease_duration=LEASE)
    with pytest.raises(ValueError, match="ready product state"):
        completion.claim(invalid.pk, lease_duration=LEASE)
    invalid.refresh_from_db()
    assert invalid.claimed_at is None
    assert completion.claim(owed.pk, lease_duration=LEASE) is not None


def test_selection_is_not_ownership_when_a_competitor_claims_before_delivery() -> None:
    owner = PrimitiveOwner.objects.create(value="ready")
    assert list(completion.available(PrimitiveOwner.objects.all(), lease_duration=LEASE)) == [owner]
    with completion.held(owner.pk, lease_duration=LEASE, retain_on_failure=True) as competitor:
        assert competitor is not None
        with completion.held(owner.pk, lease_duration=LEASE, retain_on_failure=True) as missed:
            assert missed is None
        owner.refresh_from_db()
        assert owner.claimed_at == competitor
    # A normal, unsettled exit releases even in retention mode: the next delivery
    # can acquire immediately, whereas the missed nested delivery changed nothing.
    owner.refresh_from_db()
    assert owner.claimed_at is None
    current = completion.claim(owner.pk, lease_duration=LEASE)
    assert current is not None
    assert completion.complete(owner.pk, token=current)


@pytest.mark.parametrize("retain", [False, True], ids=["default", "retained"])
def test_real_task_cancellation_propagates_across_the_sync_provider_bridge(retain: bool) -> None:
    owner = PrimitiveOwner.objects.create(value="ready")

    async def cancel_at_provider() -> None:
        entered = asyncio.Event()

        async def provider_wait() -> None:
            entered.set()
            await asyncio.Event().wait()

        # Actual Task.cancel, at an observed await boundary. TaskGroup owns cleanup.
        async with asyncio.timeout(5), asyncio.TaskGroup() as group:
            task = group.create_task(provider_wait())
            await entered.wait()
            task.cancel()
            await task

    with AsyncSingleThreadContext(), pytest.raises(asyncio.CancelledError):
        with completion.held(owner.pk, lease_duration=LEASE, retain_on_failure=retain) as token:
            assert token is not None
            async_to_sync(cancel_at_provider)()
    owner.refresh_from_db()
    assert owner.claimed_at == (token if retain else None)
    assert owner.completed_at is None
