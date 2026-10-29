"""Bind the shared convergence proofs to real buffered receipts and recovery."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
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
class BatchSnapshot:
    identities: tuple[UUID, ...]
    products: tuple[tuple[str, ...], ...]
    provider_acceptances: tuple[tuple[str, ...], ...]


@contextmanager
def buffered_convergence(capability: SendChunk) -> Iterator[ConvergentWrite]:
    # EXTERNAL SEAM: SendChunk's provider records acceptances independently of SQL.
    with WorkTableDelivery(capability).session() as delivery:

        def snapshot(identities: tuple[UUID, ...]) -> BatchSnapshot:
            # OBSERVE independent product state and the external provider ledger.
            return BatchSnapshot(
                identities,
                tuple(tuple(capability.model.objects.get(pk=pk).delivered) for pk in identities),
                tuple(tuple(SendChunk.accepted_by_work.get(pk, [])) for pk in identities),
            )

        def make(*, crash: bool) -> BatchSnapshot:
            # ARRANGE real intent; production owns all claims, receipts and state.
            rows = [capability.model.objects.create(universe="acme", bulk_keys=[str(i)]) for i in range(3)]
            for row in rows:
                make_due(row)

            def identities() -> Iterator[UUID]:
                for row in rows:
                    yield row.pk
                if crash:
                    # FAULT: process death after all three receipts, before flush.
                    raise WorkerDied("buffer lost before settlement")

            try:
                capability.attempt_many(identities(), settlement_batch_size=4)
            except WorkerDied:
                pass
            return snapshot(tuple(row.pk for row in rows))

        def recover(state: BatchSnapshot) -> BatchSnapshot:
            # REAL PRODUCTION: registered recovery and its ordinary worker tasks.
            delivery.recover()
            return snapshot(state.identities)

        def settled(state: BatchSnapshot) -> bool:
            expected = (("0",), ("1",), ("2",))
            return state.products == expected and state.provider_acceptances == expected

        yield ConvergentWrite(
            name="buffered acknowledged chunks",
            settled_state=partial(make, crash=False),
            unsettled_state=partial(make, crash=True),
            apply_evidence=recover,
            is_settled=settled,
        )


def prove_buffered_convergence(capability: SendChunk) -> None:
    # ARRANGE: fresh real rows and buffered receipts, through buffered_convergence.
    # REAL PRODUCTION: attempt_many and registered WorkTableDelivery recovery.
    # EXTERNAL SEAM: the SendChunk provider ledger and a death before batch flush.
    # OBSERVE: persisted delivered members and exact per-work provider acceptances.
    with buffered_convergence(capability) as binding:
        assert_convergent_write_contract(binding)


def bounded_settlement_proofs(capability: SendChunk) -> tuple[ExtraProof, ...]:
    return (
        ExtraProof(
            name="bounded_receipt_recovery", run=partial(prove_buffered_convergence, capability), transactional=True
        ),
        *(
            ExtraProof(
                name=f"bounded_receipt_detects_{name}",
                run=RegressionControl(
                    binding=partial(buffered_convergence, capability),
                    proof=assert_unsettled_state_converges,
                    regression=partial(fault, capability),
                    match="did not settle",
                ),
                transactional=True,
            )
            for name, fault in (("lost_recovery", _without_recovery), ("duplicate_effect", _with_duplicate_effect))
        ),
    )
