"""Reusable real-PostgreSQL bridge for the source-proved claim admission core.

Only the application callback is faulted. attempt_many, locks, predicates,
receipts, success saves, exception fallback and rollback are production code.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, connection

from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState
from due.django.runtime.settlement_batch import _SettlementBatch
from due.django.testing import make_due
from tests.harness.primitive import PrimitiveOwner
from tests.harness.self_work import SendChunk


@dataclass(frozen=True)
class SettlementCase:
    count: int
    fence: str
    fault: bool
    set_based: bool

    @property
    def name(self) -> str:
        return f"n{self.count}-{self.fence}-{'fault' if self.fault else 'healthy'}-{'set' if self.set_based else 'row'}"


@dataclass(frozen=True)
class BatchObservation:
    delivered: tuple[tuple[str, ...], ...]
    consumed: tuple[bool, ...]
    products: tuple[str, ...]
    callbacks: tuple[int, ...]
    successes: tuple[int, ...]
    sends: int
    errors: int
    rollbacks: int


def settlement_cases() -> tuple[SettlementCase, ...]:
    return tuple(
        SettlementCase(count, fence, fault, set_based)
        for count in (2, 3)
        for fence in ("none", "token", "state", "revision")
        for fault in (False, True)
        for set_based in (False, True)
    )


def assert_batch_conformance(capability: SendChunk, case: SettlementCase) -> BatchObservation:
    """Exercise a batch and observe effects through independent SQL connections."""
    rows = [capability.model.objects.create(universe="acme", bulk_keys=[str(i)]) for i in range(case.count)]
    products = [PrimitiveOwner.objects.create() for _ in rows]
    for row in rows:
        make_due(row)
    identities = [row.pk for row in rows]
    callbacks: list[int] = []
    successes: list[int] = []
    rollbacks: list[tuple[tuple[tuple[str, ...], ...], tuple[str, ...]]] = []
    provider_before = len(SendChunk.calls)
    group = _SettlementBatch._group
    flush = _SettlementBatch.flush
    batch_hook = type(capability).finalize_batch
    success_hook = type(capability).finalize_success
    faults: list[str] = []

    def snapshot():
        return (
            tuple(tuple(capability.model.objects.get(pk=pk).delivered) for pk in identities),
            tuple(PrimitiveOwner.objects.get(pk=product.pk).value for product in products),
        )

    def outside():
        close_old_connections()
        try:
            return snapshot()
        finally:
            close_old_connections()

    def read_outside():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(outside).result(timeout=10)

    initial = (tuple(() for _ in rows), tuple("before" for _ in rows))

    def finalize(instance, work, result):
        if instance is capability:
            assert connection.in_atomic_block, "callback escaped its settlement transaction"
            index = identities.index(work.pk)
            callbacks.append(index)
            # A real application write makes rollback and committed publication observable.
            PrimitiveOwner.objects.filter(pk=products[index].pk).update(value="published")
            if case.fault and index == 1:
                faults.append("persistent callback fault")
                raise ValueError("persistent callback fault")
        return batch_hook(instance, work, result)

    def success(instance, work, result):
        if instance is capability:
            assert connection.in_atomic_block
            persisted = capability.model.objects.get(pk=work.pk)
            assert persisted.state == DurableWorkState.SUCCEEDED, "finalizer preceded the success save"
            successes.append(identities.index(work.pk))
        return success_hook(instance, work, result)

    def observe_group(batch, pending):
        try:
            return group(batch, pending)
        except Exception:
            # _group has exited atomic(): no partial local write may be visible
            # from another connection, even when earlier callbacks already ran.
            rolled_back = read_outside()
            rollbacks.append(rolled_back)
            assert rolled_back == initial, ("group rollback leaked product writes", rolled_back)
            raise

    def observe_flush(batch):
        if batch.pending:
            assert not connection.in_atomic_block
            assert [attempts_for(row).get().outcome for row in rows] == ["applied"] * case.count
            assert len(SendChunk.calls) - provider_before == case.count
            assert read_outside() == initial, "product published before durable receipts were flushed"
        return flush(batch)

    def inputs():
        yield identities[0]
        changes = {
            "none": {},
            "token": {"fencing_token": uuid4()},
            "state": {"state": DurableWorkState.UNKNOWN, "lease_expires_at": None},
            "revision": {"desired_revision": rows[0].desired_revision + 1},
        }[case.fence]
        if changes:
            capability.model.objects.filter(pk=identities[0]).update(**changes)
        yield from identities[1:]

    errors = 0
    failure: ExceptionGroup | None = None
    with (
        patch.object(type(capability), "finalize_batch", finalize),
        patch.object(type(capability), "finalize_success", success),
        patch.object(_SettlementBatch, "_group", observe_group),
        patch.object(_SettlementBatch, "flush", observe_flush),
    ):
        try:
            outcomes = capability.attempt_many(inputs(), settlement_batch_size=case.count, set_based=case.set_based)
        except ExceptionGroup as error:
            failure = error
            errors = len(error.exceptions)
            outcomes = None

    delivered, published = read_outside()
    observed = BatchObservation(
        delivered,
        tuple(not attempts_for(row).get().repair_pending for row in rows),
        published,
        tuple(callbacks.count(i) for i in range(case.count)),
        tuple(successes.count(i) for i in range(case.count)),
        len(SendChunk.calls) - provider_before,
        errors,
        len(rollbacks),
    )
    owns = tuple(i != 0 or case.fence == "none" for i in range(case.count))
    done = tuple(owns[i] and not (case.fault and i == 1) for i in range(case.count))
    expected = BatchObservation(
        tuple((str(i),) if done[i] else () for i in range(case.count)),
        done,
        tuple("published" if done[i] else "before" for i in range(case.count)),
        tuple((2 if case.fault and i <= 1 else 1) if owns[i] else 0 for i in range(case.count)),
        tuple((2 if case.fault and i == 0 and not case.set_based else 1) if done[i] else 0 for i in range(case.count)),
        case.count,
        int(case.fault),
        int(case.fault),
    )
    assert observed == expected, ("claim settlement conformance", case, observed, expected, failure)
    if failure is not None:
        assert case.fault and all(
            type(item) is ValueError and str(item) == "persistent callback fault" for item in failure.exceptions
        ), ("unexpected settlement failure", failure)
    assert len(faults) == (2 if case.fault else 0), "persistent fault did not reach group and fallback"
    if outcomes is not None:
        assert outcomes == dict(zip(identities, owns, strict=True))
    assert [SendChunk.accepted_by_work[row.pk] for row in rows] == [[str(i)] for i in range(case.count)]
    return observed
