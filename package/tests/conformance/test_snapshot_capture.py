"""Snapshot proofs distinguish queued reads from already captured application facts."""

from contextlib import nullcontext
from unittest.mock import patch

import pytest
from pytest_obligation.profiles.eventual_convergence import (
    assert_current_snapshot_writes,
    assert_superseded_snapshot_does_not_write,
)

from due.django.conformance.suite import SupersessionExample, snapshot_bindings
from tests.harness.primitive import PrimitiveEffect, PrimitiveOwner, primitive, record
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


@pytest.mark.parametrize("superseded", [False, True])
@pytest.mark.parametrize("broken", [False, True])
def test_after_apply_snapshot_checks_real_publication_and_wake_fencing(superseded, broken):
    work = record()
    example = SupersessionExample(
        work=work,
        change=lambda: primitive.wake(work.pk),
        observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
        after_apply=True,
    )
    mutation = (
        patch.object(PrimitiveEffect, "wake", lambda self, work_id: None)
        if superseded
        else patch.object(PrimitiveEffect, "finalize_success", lambda self, value, result: None)
    )
    proof = assert_superseded_snapshot_does_not_write if superseded else assert_current_snapshot_writes
    message = "superseded snapshot wrote back" if superseded else "nothing observable changed"
    with (
        mutation if broken else nullcontext(),
        snapshot_bindings(primitive, lambda: example) as binding,
        pytest.raises(AssertionError, match=message) if broken else nullcontext(),
    ):
        proof(binding)
    assert PrimitiveEffect.calls == ["after"], "capture must execute the real provider call exactly once"


def test_after_apply_capture_releases_its_worker_when_the_proof_raises():
    work = record()
    example = SupersessionExample(
        work=work,
        change=lambda: primitive.wake(work.pk),
        observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
        after_apply=True,
    )
    original_apply = PrimitiveEffect.apply
    with pytest.raises(RuntimeError, match="proof interrupted"):
        with snapshot_bindings(primitive, lambda: example) as binding:
            binding.take_snapshot()
            assert example.observe() == "before"
            raise RuntimeError("proof interrupted")
    assert PrimitiveEffect.apply is original_apply
    assert example.observe() == "after", "cleanup must release and join the real worker before dropping its tables"
