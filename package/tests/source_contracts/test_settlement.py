"""Mandatory native counterpart of the pure ownership and admission proofs."""

import pytest

from due.django.runtime.settlement_batch import _SettlementBatch
from due.django.testing import make_due
from tests.harness.claim_settlement import assert_batch_conformance, settlement_cases
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import composed_inline_chunk_environment as composed_inline_chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("case", settlement_cases(), ids=lambda case: case.name)
def test_postgres_callback_matrix(composed_inline_chunk, case):
    assert_batch_conformance(composed_inline_chunk, case)


@pytest.mark.parametrize("change", ["missing", "duplicate"])
def test_locked_row_can_admit_at_most_one_buffered_callback(composed_inline_chunk, monkeypatch, change):
    capability = composed_inline_chunk
    rows = [capability.model.objects.create(universe="acme", bulk_keys=[str(i)]) for i in range(2)]
    batch = _SettlementBatch(3)
    entered = []
    original = type(capability).finalize_batch

    def observe(instance, work, result):
        entered.append(work.pk)
        return original(instance, work, result)

    monkeypatch.setattr(type(capability), "finalize_batch", observe)
    for row in rows:
        make_due(row)
        assert capability._attempt_execution(row.pk, completion=batch.add)
    if change == "missing":
        rows[0].delete()
    else:
        batch.pending.insert(1, batch.pending[0])
    batch.flush()
    expected = [rows[1].pk] if change == "missing" else [row.pk for row in rows]
    assert entered == expected, "absent or already-consumed locked row entered a callback"
    for pk in expected:
        assert capability.model.objects.get(pk=pk).delivered
