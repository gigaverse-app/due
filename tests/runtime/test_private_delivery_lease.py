"""
Reserve before publication, consume each delivery once, and recover lost work.

Design and proof boundaries: docs/architecture.md#verification.
See the guide's verification module map for related production-bound suites.
"""

from datetime import datetime, timedelta

import pytest
from django.utils import timezone

from due.django.runtime._delivery import RecoveryLease
from due.django.runtime.recovery_pass import RecoveryIncomplete
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

lease = RecoveryLease(model=PrimitiveOwner, claimed_at="claimed_at", duration=timedelta(minutes=5))
pytestmark = pytest.mark.usefixtures("isolated_primitive")


def test_recovery_excludes_outstanding_delivery_and_consumes_it_once() -> None:
    owner = PrimitiveOwner.objects.create()
    messages: list[tuple[int, datetime]] = []
    calls: list[int] = []
    due = PrimitiveOwner.objects.order_by("pk")

    def publish(identity: int, token: datetime) -> None:
        messages.append((identity, token))
        assert lease.recover(due, publish=publish, batch_size=10) == 0

    assert lease.recover(due, publish=publish, batch_size=10) == 1
    identity, token = messages[0]

    def execute(identity: int) -> None:
        calls.append(identity)
        assert lease.recover(due, publish=publish, batch_size=10) == 0
        assert not lease.attempt_execution(identity, token=token, run=calls.append)
        assert not lease.attempt_execution(identity, run=calls.append)  # Legacy delivery.

    assert lease.attempt_execution(identity, token=token, run=execute)
    assert calls == [owner.pk]
    assert not lease.attempt_execution(identity, token=token, run=calls.append)
    owner.refresh_from_db()
    assert owner.claimed_at is None


def test_lost_publication_recovers_after_expiry_without_starving_the_next_row() -> None:
    owners = PrimitiveOwner.objects.bulk_create([PrimitiveOwner(), PrimitiveOwner()])
    messages: list[tuple[int, datetime]] = []
    observed: list[int] = []
    due = PrimitiveOwner.objects.order_by("pk")

    def publish(identity: int, token: datetime) -> None:
        messages.append((identity, token))
        if identity == owners[0].pk:
            raise OSError("broker may have accepted")

    with pytest.raises(RecoveryIncomplete):  # The ambiguous publication fails its tick loudly.
        lease.recover(due, publish=publish, batch_size=1)
    assert lease.recover(due, publish=publish, batch_size=1, observe_outstanding=observed.append) == 1
    assert observed == [2]  # Includes outstanding delivery.
    assert lease.recover(due, publish=publish, batch_size=1) == 0
    old_identity, old_token = messages[0]
    PrimitiveOwner.objects.filter(pk=old_identity).update(claimed_at=timezone.now() - timedelta(minutes=6))
    with pytest.raises(RecoveryIncomplete):
        lease.recover(due, publish=publish, batch_size=1)
    assert len(messages) == 3
    calls: list[int] = []
    assert not lease.attempt_execution(old_identity, token=old_token, run=calls.append)
    assert lease.attempt_execution(old_identity, token=messages[-1][1], run=calls.append)
    assert calls == [old_identity]


def test_late_execution_cannot_release_a_successors_reservation() -> None:
    owner = PrimitiveOwner.objects.create()
    messages: list[datetime] = []

    def run(identity: int) -> None:
        PrimitiveOwner.objects.filter(pk=identity).update(claimed_at=timezone.now() - timedelta(minutes=6))
        assert (
            lease.recover(
                PrimitiveOwner.objects.order_by("pk"),
                publish=lambda identity, token: messages.append(token),
                batch_size=1,
            )
            == 1
        )

    assert lease.attempt_execution(owner.pk, run=run)
    owner.refresh_from_db()
    assert owner.claimed_at == messages[0]
