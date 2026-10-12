"""
Existing canonical rows can own completion without a second work graph.

Design and proof boundaries: docs/technical-design/durable-work-library.md#verification.
See the guide's verification module map for related production-bound suites.
"""

import asyncio
from datetime import datetime, timedelta

import pytest
from django.db import connection
from django.utils import timezone

from due.django.native.completion import CompletionLease
from due.django.runtime.coordination import WorkOwnershipLost
from due.django.testing import WorkerDied
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)


def require_ready(owner: PrimitiveOwner) -> None:
    if owner.value != "ready":
        raise ValueError("completion requires ready product state")


completion = CompletionLease(
    model=PrimitiveOwner,
    claimed_at="claimed_at",
    completed_at="completed_at",
    validate=require_ready,
)


@pytest.mark.usefixtures("isolated_primitive")
def test_invalid_product_state_cannot_acquire_completion_authority() -> None:
    owner = PrimitiveOwner.objects.create()
    with pytest.raises(ValueError, match="ready product state"):
        completion.claim(owner.pk)
    owner.refresh_from_db()
    assert owner.claimed_at is None
    assert owner.completed_at is None


@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("settle", ["complete", "release"])
def test_stale_completion_cannot_mutate_a_successors_claim(settle: str) -> None:
    owner = PrimitiveOwner.objects.create(value="ready")
    stale = completion.claim(owner.pk)
    assert stale is not None
    assert completion.claim(owner.pk) is None
    PrimitiveOwner.objects.filter(pk=owner.pk).update(claimed_at=timezone.now() - timedelta(minutes=2))
    current = completion.claim(owner.pk)
    assert current is not None and current != stale
    operation = completion.complete if settle == "complete" else completion.release
    assert not operation(owner.pk, token=stale)
    owner.refresh_from_db()
    assert owner.claimed_at == current
    assert owner.completed_at is None
    assert operation(owner.pk, token=current)
    owner.refresh_from_db()
    assert owner.claimed_at is None
    assert (owner.completed_at is not None) == (settle == "complete")


@pytest.mark.usefixtures("isolated_primitive")
def test_completed_receipt_survives_redelivery() -> None:
    owner = PrimitiveOwner.objects.create(value="ready")
    token = completion.claim(owner.pk)
    assert token is not None
    assert completion.complete(owner.pk, token=token)
    assert completion.claim(owner.pk) is None
    assert not completion.release(owner.pk, token=token)


def test_completion_columns_must_be_distinct_nullable_timestamps() -> None:
    with pytest.raises(ValueError, match="nullable DateTimeField"):
        CompletionLease(model=PrimitiveOwner, claimed_at="value", completed_at="completed_at", validate=require_ready)
    with pytest.raises(ValueError, match="distinct"):
        CompletionLease(
            model=PrimitiveOwner, claimed_at="claimed_at", completed_at="claimed_at", validate=require_ready
        )


@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("fail", [False, True])
def test_native_product_changes_and_completion_receipt_share_one_transaction(fail: bool) -> None:
    owner = PrimitiveOwner.objects.create(value="ready")
    token = completion.claim(owner.pk)
    assert token is not None

    def publish(row: PrimitiveOwner) -> None:
        assert connection.in_atomic_block
        row.value = "published"
        row.save(update_fields=["value"])
        if fail:
            raise RuntimeError("publication failed")

    if fail:
        with pytest.raises(RuntimeError, match="publication failed"):
            completion.complete(owner.pk, token=token, finalize=publish)
    else:
        assert completion.complete(owner.pk, token=token, finalize=publish)
        assert completion.claim(owner.pk) is None
    owner.refresh_from_db()
    assert owner.value == ("ready" if fail else "published")
    assert (owner.completed_at is None) == fail
    assert (owner.claimed_at == token) == fail


@pytest.mark.usefixtures("isolated_primitive")
def test_stale_native_completion_cannot_enter_product_finalization() -> None:
    owner = PrimitiveOwner.objects.create(value="ready")
    stale = completion.claim(owner.pk)
    assert stale is not None
    PrimitiveOwner.objects.filter(pk=owner.pk).update(claimed_at=timezone.now() - timedelta(minutes=2))
    current = completion.claim(owner.pk)
    assert current is not None
    published: list[int] = []

    def publish(row: PrimitiveOwner) -> None:
        published.append(row.pk)

    assert not completion.complete(owner.pk, token=stale, finalize=publish)
    assert published == []
    assert completion.complete(owner.pk, token=current, finalize=publish)
    assert published == [owner.pk]


@pytest.mark.usefixtures("isolated_primitive")
def test_finalizer_cannot_commit_product_changes_without_its_receipt() -> None:
    owner = PrimitiveOwner.objects.create(value="ready")
    token = completion.claim(owner.pk)
    assert token is not None

    def invalid_publish(row: PrimitiveOwner) -> None:
        row.value = "published"
        row.claimed_at = None
        row.save(update_fields=["value", "claimed_at"])

    with pytest.raises(WorkOwnershipLost, match="completion authority"):
        completion.complete(owner.pk, token=token, finalize=invalid_publish)
    owner.refresh_from_db()
    assert owner.value == "ready"
    assert owner.claimed_at == token
    assert owner.completed_at is None


@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("outcome", ["complete", "abandon", "worker_death", "successor"])
def test_held_completion_releases_only_its_own_uncompleted_claim(outcome: str) -> None:
    from contextlib import nullcontext

    from due.django.testing import WorkerDied

    owner = PrimitiveOwner.objects.create(value="ready")
    current = None
    expected = pytest.raises(WorkerDied) if outcome == "worker_death" else nullcontext()
    with expected:
        with completion.held(owner.pk) as token:
            assert token is not None
            with completion.held(owner.pk) as competitor:
                assert competitor is None
            if outcome == "complete":
                assert completion.complete(owner.pk, token=token)
            elif outcome == "worker_death":
                raise WorkerDied()
            elif outcome == "successor":
                PrimitiveOwner.objects.filter(pk=owner.pk).update(claimed_at=timezone.now() - timedelta(minutes=2))
                current = completion.claim(owner.pk)
                assert current is not None and current != token
    owner.refresh_from_db()
    assert owner.claimed_at == current
    assert (owner.completed_at is not None) == (outcome == "complete")


@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("retain_on_failure", [False, True])
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("provider failed"), asyncio.CancelledError(), WorkerDied()],
    ids=["exception", "cancellation", "worker_death"],
)
def test_retained_failure_keeps_the_original_claim_until_its_original_expiry(
    failure: BaseException, retain_on_failure: bool
) -> None:
    owner = PrimitiveOwner.objects.create(value="ready")
    tokens: list[datetime] = []
    with pytest.raises(type(failure)):
        with completion.held(owner.pk, retain_on_failure=retain_on_failure) as token:
            assert token is not None
            tokens.append(token)
            raise failure
    owner.refresh_from_db()
    if not retain_on_failure:
        assert owner.claimed_at is None
        return
    # The failure writes nothing: the claim is still the original token, so its
    # expiry is still claim time + lease, never failure time + lease.
    assert owner.claimed_at == tokens[0]
    assert completion.claim(owner.pk) is None
    PrimitiveOwner.objects.filter(pk=owner.pk).update(claimed_at=timezone.now() - timedelta(minutes=2))
    assert completion.claim(owner.pk) is not None


@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("settle", ["complete", "normal_exit"])
def test_retained_claim_still_settles_on_normal_exit(settle: str) -> None:
    owner = PrimitiveOwner.objects.create(value="ready")
    with completion.held(owner.pk, retain_on_failure=True) as token:
        assert token is not None
        if settle == "complete":
            assert completion.complete(owner.pk, token=token)
    owner.refresh_from_db()
    assert owner.claimed_at is None
    assert (owner.completed_at is not None) == (settle == "complete")


@pytest.mark.usefixtures("isolated_primitive")
def test_available_admits_exactly_the_rows_claim_would_admit() -> None:
    lease = timedelta(minutes=1)
    unclaimed = PrimitiveOwner.objects.create(value="ready")
    live = PrimitiveOwner.objects.create(value="ready", claimed_at=timezone.now())
    expired = PrimitiveOwner.objects.create(value="ready", claimed_at=timezone.now() - timedelta(minutes=2))
    completed = PrimitiveOwner.objects.create(value="ready", completed_at=timezone.now())
    rows = [unclaimed, live, expired, completed]

    available = set(
        completion.available(PrimitiveOwner.objects.all(), lease_duration=lease).values_list("pk", flat=True)
    )

    assert available == {unclaimed.pk, expired.pk}
    for row in rows:
        assert (completion.claim(row.pk, lease_duration=lease) is not None) == (row.pk in available)


def test_available_requires_an_unsliced_query_for_its_model() -> None:
    with pytest.raises(ValueError, match="unsliced query"):
        completion.available(PrimitiveOwner.objects.all()[:1], lease_duration=timedelta(minutes=1))
    with pytest.raises(ValueError, match="positive"):
        completion.available(PrimitiveOwner.objects.all(), lease_duration=timedelta(0))
