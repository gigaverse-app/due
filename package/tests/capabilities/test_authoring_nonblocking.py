"""Nonblocking admission must not wait on, or skip, an existing work row."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import close_old_connections, transaction

from due.django.public import WorkOwnerBusy
from tests.harness.primitive import PrimitiveWork, primitive, record
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


def test_nonblocking_admission_refuses_a_busy_work_row_instead_of_waiting_or_inserting() -> None:
    work = record()
    assert work.owner_id is not None
    held, release = Event(), Event()

    def hold_work() -> None:
        close_old_connections()
        try:
            with transaction.atomic():
                PrimitiveWork.objects.select_for_update().get(pk=work.pk)
                held.set()
                assert release.wait(10), "test never released the competing work lock"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as executor:
        competing = executor.submit(hold_work)
        try:
            assert held.wait(5), "competing connection never acquired its lock"
            with pytest.raises(WorkOwnerBusy):
                with primitive.atomic(work.owner_id, notify_worker=False, wait_for_owner=False):
                    pytest.fail("a busy work row must not appear to be absent")
        finally:
            release.set()
            competing.result(timeout=5)
    # Positive control: once released, the same API finds the SAME obligation.
    with primitive.atomic(work.owner_id, notify_worker=False, wait_for_owner=False) as pending:
        assert pending.existing_work is not None
        assert pending.existing_work.pk == work.pk
    assert PrimitiveWork.objects.count() == 1
