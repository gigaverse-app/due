"""Discard process-local evidence and prove registered recovery still settles it."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from unittest.mock import patch
from uuid import UUID

from pytest_obligation.contract import ExtraProof
from pytest_obligation.profiles.eventual_convergence import (
    ConvergentWrite,
    assert_convergent_write_contract,
    assert_unsettled_state_converges,
)
from pytest_obligation.regression_controls import RegressionControl
from pytest_obligation.worker_death import WorkerDied

from due.django.conformance.delivery import WorkTableDelivery
from due.django.conformance.regressions import _with_duplicate_effect, _without_recovery
from due.django.testing import make_due
from tests.harness.self_work import SendChunk


@dataclass(frozen=True)
class ReceiptSnapshot:
    identity: UUID
    delivered: tuple[str, ...]
    accepted: tuple[str, ...]


@contextmanager
def lost_handoff(capability: SendChunk) -> Iterator[ConvergentWrite]:
    with WorkTableDelivery(capability).session() as delivery:

        def observe(identity: UUID) -> ReceiptSnapshot:
            # OBSERVE independent product state and the provider's acceptance ledger.
            return ReceiptSnapshot(
                identity,
                tuple(capability.model.objects.get(pk=identity).delivered),
                tuple(SendChunk.accepted_by_work.get(identity, [])),
            )

        def make(*, crash: bool) -> ReceiptSnapshot:
            # ARRANGE real product intent. Production owns claims and receipt writes.
            work = capability.model.objects.create(universe="acme", bulk_keys=["a", "b"])
            make_due(work)
            original = SendChunk.apply

            def interrupt(instance, current, context):
                outcome = original(instance, current, context)
                if crash:
                    # FAULT: the committed receipt survives, the entire context is lost.
                    raise WorkerDied("lost committed evidence handoff")
                return outcome

            # EXTERNAL SEAM: SendChunk records actual provider calls independently.
            with patch.object(SendChunk, "apply", interrupt):
                try:
                    capability.attempt_execution(work.pk)
                except WorkerDied:
                    pass
            return observe(work.pk)

        def recover(state: ReceiptSnapshot) -> ReceiptSnapshot:
            # REAL PRODUCTION: registered recovery, without the interrupted context.
            delivery.recover()
            return observe(state.identity)

        yield ConvergentWrite(
            name="lost committed evidence handoff",
            settled_state=partial(make, crash=False),
            unsettled_state=partial(make, crash=True),
            apply_evidence=recover,
            is_settled=lambda state: state.delivered == state.accepted == ("a", "b"),
        )


def prove_lost_handoff(capability: SendChunk) -> None:
    # ARRANGE: fresh product rows in lost_handoff.
    # REAL PRODUCTION: attempt_execution and registered WorkTableDelivery recovery.
    # EXTERNAL SEAM: SendChunk's provider ledger; death after its committed receipt.
    # OBSERVE: durable delivered members and exact external acceptance counts.
    with lost_handoff(capability) as binding:
        assert_convergent_write_contract(binding)


def committed_evidence_proofs(capability: SendChunk) -> tuple[ExtraProof, ...]:
    return (
        ExtraProof(name="lost_committed_evidence", run=partial(prove_lost_handoff, capability), transactional=True),
        *(
            ExtraProof(
                name=f"lost_handoff_detects_{name}",
                run=RegressionControl(
                    binding=partial(lost_handoff, capability),
                    proof=assert_unsettled_state_converges,
                    regression=partial(fault, capability),
                    match="did not settle",
                ),
                transactional=True,
            )
            for name, fault in (("lost_recovery", _without_recovery), ("duplicate_effect", _with_duplicate_effect))
        ),
    )
