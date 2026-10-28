"""Real database observations of legacy bulk settlement hook boundaries."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from unittest.mock import patch

from django.db import close_old_connections

from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState, EffectAttempt, EffectAttemptOutcome
from due.django.testing import make_due
from tests.harness.self_work import ChunkFields, SendChunk


@dataclass(frozen=True)
class SettlementSnapshot:
    state: str
    delivered: tuple[str, ...]
    progress: tuple[str, ...]
    outcome: str


def snapshot(work: ChunkFields) -> SettlementSnapshot:
    current = type(work).objects.get(pk=work.pk)
    attempt = attempts_for(current).get()
    return SettlementSnapshot(
        current.state,
        tuple(current.delivered),
        tuple(item["key"] for item in current.bulk_progress),
        attempt.outcome,
    )


def outside_transaction(work: ChunkFields) -> SettlementSnapshot:
    close_old_connections()
    try:
        return snapshot(work)
    finally:
        close_old_connections()


def assert_settlement_boundaries(capability: SendChunk, *, fail_at: str) -> None:
    """Observe real writes from hooks and another connection, then recover."""
    work = capability.model.objects.create(universe="acme", bulk_keys=["a", "b"])
    make_due(work)
    seen: list[str] = []
    failing = True
    expected_before = DurableWorkState.CLAIMED
    original_batch = type(capability).finalize_batch
    original_success = type(capability).finalize_success

    def observe(current: ChunkFields, phase: str) -> None:
        expected_state = expected_before if phase == "batch" else DurableWorkState.SUCCEEDED
        inside = snapshot(current)
        assert inside == SettlementSnapshot(expected_state, ("a", "b"), ("a", "b"), EffectAttemptOutcome.APPLIED), (
            "settlement hook lost persisted progress or state ordering",
            phase,
            inside,
        )
        assert current.state == expected_state and current.delivered == ["a", "b"]
        with ThreadPoolExecutor(max_workers=1) as pool:
            outside = pool.submit(outside_transaction, current).result(timeout=5)
        assert outside == SettlementSnapshot(expected_before, (), (), EffectAttemptOutcome.APPLIED), (
            "receipt must commit before product settlement",
            outside,
        )
        seen.append(phase)
        if failing and phase == fail_at:
            raise RuntimeError("injected product hook failure")

    def batch(instance: SendChunk, current: ChunkFields, result) -> None:
        if instance is capability:
            observe(current, "batch")
        original_batch(instance, current, result)

    def success(instance: SendChunk, current: ChunkFields, attempt: EffectAttempt) -> None:
        if instance is capability:
            observe(current, "success")
        original_success(instance, current, attempt)

    with (
        patch.object(type(capability), "finalize_batch", batch),
        patch.object(type(capability), "finalize_success", success),
    ):
        try:
            capability.attempt_execution(work.pk)
        except RuntimeError as error:
            assert str(error) == "injected product hook failure"
        else:
            raise AssertionError("the selected product hook was not reached")
        assert seen == (["batch"] if fail_at == "batch" else ["batch", "success"])
        assert snapshot(work) == SettlementSnapshot(DurableWorkState.UNKNOWN, (), (), EffectAttemptOutcome.APPLIED)
        assert attempts_for(work).get().repair_pending
        assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]

        failing = False
        expected_before = DurableWorkState.UNKNOWN
        capability.derive()
    assert seen[-2:] == ["batch", "success"]
    assert snapshot(work) == SettlementSnapshot(
        DurableWorkState.SUCCEEDED, ("a", "b"), ("a", "b"), EffectAttemptOutcome.APPLIED
    )
    assert not attempts_for(work).get().repair_pending
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b"]
    assert sum(pk == work.pk for pk, _ in SendChunk.calls) == 1
