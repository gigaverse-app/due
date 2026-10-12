"""Shared evidence proofs name a not-yet-due example instead of a missing effect."""

from datetime import timedelta

import pytest

from due.django.conformance.scenarios import (
    assert_receipt_repairs_product,
    assert_uncertainty_blocks_resend,
)
from due.django.public import database_now
from tests.harness.operation import (
    Publication,
    delivery_example,
    record,
)
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = pytest.mark.django_db(transaction=True)

#: A host clock ahead of PostgreSQL's makes "due now" work due only in the future.
HOST_CLOCK_LEAD = timedelta(minutes=1)


@pytest.mark.parametrize("proof", [assert_receipt_repairs_product, assert_uncertainty_blocks_resend])
def test_evidence_proof_rejects_work_not_due_on_database_clock(publication, proof):
    work = record(publication)
    type(work).objects.filter(pk=work.pk).update(available_at=database_now() + HOST_CLOCK_LEAD)
    with pytest.raises(AssertionError, match="not due on the database clock"):
        proof(publication, delivery_example(work, expected="applied"))
    assert Publication.calls == [], "a not-due example reached provider I/O"
