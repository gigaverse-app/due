"""
The recorder sees real ORM row locks per transaction and catches a reversed pair.

Design and proof boundaries: docs/architecture.md#verification.
"""

import pytest
from django.db import transaction
from pytest_obligation.integrations.django.lock_order import assert_consistent_lock_order, record_lock_order

from tests.harness.primitive import (
    PrimitiveOwner,
    PrimitiveWork,
    record,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)


@pytest.mark.usefixtures("isolated_primitive")
def test_recorder_captures_first_lock_order_per_outermost_transaction() -> None:
    work = record()
    with record_lock_order("probe") as recorder:
        with transaction.atomic():
            PrimitiveOwner.objects.select_for_update().get(pk=work.owner_id)
            with transaction.atomic():  # A savepoint does not start a new sequence.
                PrimitiveWork.objects.select_for_update().get(pk=work.pk)
                PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="again")
        with transaction.atomic():
            PrimitiveWork.objects.filter(pk=work.pk).update(payload="x")  # One table carries no order.
    assert [(item.label, item.tables) for item in recorder.sequences] == [
        ("probe", ("tests_primitiveowner", "tests_primitivework"))
    ]


@pytest.mark.usefixtures("isolated_primitive")
def test_reversed_lock_order_across_two_production_shaped_transactions_is_rejected() -> None:
    work = record()
    with record_lock_order("owner first") as forward:
        with transaction.atomic():
            PrimitiveOwner.objects.select_for_update().get(pk=work.owner_id)
            PrimitiveWork.objects.select_for_update().get(pk=work.pk)
    with record_lock_order("work first") as reverse:
        with transaction.atomic():
            PrimitiveWork.objects.select_for_update().get(pk=work.pk)
            PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="late")
    assert_consistent_lock_order(forward.sequences)
    with pytest.raises(AssertionError, match="work first"):
        assert_consistent_lock_order([*forward.sequences, *reverse.sequences])


@pytest.mark.usefixtures("isolated_primitive")
def test_the_primitive_runtime_locks_owner_before_work_everywhere() -> None:
    from tests.harness.primitive import PrimitiveEffect, primitive

    with record_lock_order("primitive runtime") as recorder:
        record()
        primitive.recover()
        work_id, token = PrimitiveEffect.deliveries[-1]
        assert primitive.attempt_execution(work_id, token)
    assert recorder.sequences, "positive control: the runtime must lock more than one table per transaction"
    assert_consistent_lock_order(recorder.sequences)
    assert all(item.tables[0] == "tests_primitiveowner" for item in recorder.sequences if len(item.tables) > 1)
