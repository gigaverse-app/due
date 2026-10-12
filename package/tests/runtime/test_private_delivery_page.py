"""
A bounded page must survive a poisoned identity without inventing progress.

Design and proof boundaries: docs/architecture.md#verification.
See the guide's verification module map for related production-bound suites.
"""

import pytest

from due.django.runtime._delivery import dispatch_recovery_page
from due.django.runtime.recovery_pass import RecoveryIncomplete, RecoveryPass
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)


@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("accepted", [True, None])
def test_one_failed_and_one_unclaimed_row_do_not_hide_later_work(accepted: bool | None) -> None:
    rows = PrimitiveOwner.objects.bulk_create([PrimitiveOwner() for _ in range(5)])
    delivered: list[int] = []
    observed: list[int] = []

    def dispatch(identity: int) -> bool | None:
        if identity == rows[0].pk:
            raise OSError("lost broker acknowledgement")
        if identity == rows[1].pk:
            return False
        delivered.append(identity)
        return accepted

    tick = RecoveryPass("tests.primitiveowner")
    result = dispatch_recovery_page(
        PrimitiveOwner.objects.order_by("pk"),
        dispatch=dispatch,
        batch_size=4,
        observe_outstanding=observed.append,
        recovery=tick,
    )
    assert observed == [5]
    assert result == 2
    assert delivered == [row.pk for row in rows[2:4]]
    assert [type(error) for error in tick.errors] == [OSError]  # Recorded in the caller's tick, not dropped.
    assert PrimitiveOwner.objects.count() == 5

    # Called on its own, the page still finishes and then raises what it isolated.
    delivered.clear()
    with pytest.raises(RecoveryIncomplete) as raised:
        dispatch_recovery_page(PrimitiveOwner.objects.order_by("pk"), dispatch=dispatch, batch_size=4)
    assert raised.group_contains(OSError, match="lost broker acknowledgement")
    assert delivered == [row.pk for row in rows[2:4]]


@pytest.mark.usefixtures("isolated_primitive")
def test_empty_recovery_explicitly_reports_zero() -> None:
    observed: list[int] = []

    def unexpected(identity: int) -> bool:
        raise AssertionError(f"empty recovery invented identity {identity}")

    assert (
        dispatch_recovery_page(
            PrimitiveOwner.objects.order_by("pk"),
            dispatch=unexpected,
            batch_size=4,
            observe_outstanding=observed.append,
        )
        == 0
    )
    assert observed == [0]


def test_paging_has_one_owner() -> None:
    with pytest.raises(ValueError, match="unsliced"):
        dispatch_recovery_page(PrimitiveOwner.objects.all()[:1], dispatch=bool, batch_size=4)
    with pytest.raises(ValueError, match="positive"):
        dispatch_recovery_page(PrimitiveOwner.objects.all(), dispatch=bool, batch_size=0)
