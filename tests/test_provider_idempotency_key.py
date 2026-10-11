"""A provider deduplication key names one revision of one row, beyond a single database."""

from datetime import timedelta

import pytest

from tests.harness.primitive import PrimitiveWork, primitive, record
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


def test_retries_reuse_the_key_and_a_revised_input_gets_a_new_one() -> None:
    work = record()
    key = work.provider_idempotency_key()
    primitive.complete(work)
    assert PrimitiveWork.objects.get(pk=work.pk).provider_idempotency_key() == key
    revised = record("revised", owner=work.owner)
    assert revised.pk == work.pk
    assert revised.provider_idempotency_key() != key


def test_the_same_primary_key_in_another_database_gets_another_key() -> None:
    work = record()
    key = work.provider_idempotency_key()
    # A reset or restored database reissues primary keys; its rows were admitted at other times.
    PrimitiveWork.objects.filter(pk=work.pk).update(created_at=work.created_at + timedelta(microseconds=1))
    reissued = PrimitiveWork.objects.get(pk=work.pk)
    assert (reissued.pk, reissued.desired_revision) == (work.pk, work.desired_revision)
    assert reissued.provider_idempotency_key() != key
