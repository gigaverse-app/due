"""A cheaper authority lookup still reads and locks the current database row."""

from datetime import timedelta
from uuid import uuid7

import pytest
from django.db import connection, models, transaction
from django.test.utils import CaptureQueriesContext

from due.django.models import DurableWorkState
from due.django.runtime.coordination import claim_work
from due.django.testing import make_due
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import inline_chunk_environment as inline_chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("change", ["token", "revision", "state", "product"])
def test_fixed_lookup_observes_current_authority_and_product(inline_chunk, change):
    row = inline_chunk.model.objects.create(universe="acme", bulk_keys=["a"])
    make_due(row)
    claim = claim_work(type(row), row.pk, lease_duration=timedelta(minutes=1))
    assert claim is not None
    with transaction.atomic():
        first = claim.locked_work()
        assert first is not None
        assert first is not row and first is not claim.work
    changes = {
        "token": {"fencing_token": uuid7()},
        "revision": {"desired_revision": 2},
        "state": {"state": DurableWorkState.UNKNOWN, "lease_expires_at": None},
        "product": {"product_status": "new product state"},
    }
    type(row).objects.filter(pk=row.pk).update(**changes[change])
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        current = claim.locked_work()
    assert len(queries) == 1
    assert "FOR UPDATE" in queries[0]["sql"]
    if change == "product":
        assert current is not None and current.product_status == "new product state"
        assert current.created_at == row.created_at
        assert current.bulk_keys == ["a"]
    else:
        assert current is None


def test_authority_lookup_requires_transaction(inline_chunk):
    row = inline_chunk.model.objects.create(universe="acme", bulk_keys=["a"])
    make_due(row)
    claim = claim_work(type(row), row.pk, lease_duration=timedelta(minutes=1))
    assert claim is not None
    with pytest.raises(transaction.TransactionManagementError):
        claim.locked_work()


def test_custom_manager_visibility_is_preserved(inline_chunk, monkeypatch):
    row = inline_chunk.model.objects.create(universe="acme", bulk_keys=["a"])
    make_due(row)
    claim = claim_work(type(row), row.pk, lease_duration=timedelta(minutes=1))
    assert claim is not None

    class HiddenManager(models.Manager):
        def get_queryset(self):
            return super().get_queryset().filter(product_status="visible")

    manager = HiddenManager()
    manager.model = type(row)
    # ARRANGE an adopter's scoped manager. Its visibility is authoritative.
    monkeypatch.setattr(type(row), "objects", manager)
    with transaction.atomic():
        assert claim.locked_work() is None
    type(row)._base_manager.filter(pk=row.pk).update(product_status="visible")
    with transaction.atomic():
        current = claim.locked_work()
        assert current is not None and current.pk == row.pk
