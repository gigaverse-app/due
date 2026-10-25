"""Bulk-specific evidence histories declared to obligation's shared scheduler."""

import json
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from unittest.mock import patch

import pytest
from asgiref.sync import AsyncSingleThreadContext
from pydantic import BaseModel, ConfigDict
from pytest_obligation.interleavings import (
    EvidenceArrival,
    EvidenceConfluence,
    EvidenceExpectation,
    EvidenceRetry,
    EvidenceSession,
)

from due.bulk import BulkOutcomeState, BulkResult
from due.django.conformance.background import frozen_scheduler_clock, harness_owned_background_work
from due.django.conformance.interleavings import connection_scope
from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState
from due.django.testing import make_due
from due.errors import EffectOutcomeUnknownError
from tests.harness.bulk import BatchOperation, BatchReceipt, batch, record, result
from tests.harness.primitive import PrimitiveOwner


class BatchObservation(BaseModel):
    model_config = ConfigDict(frozen=True)
    product: str
    work_state: str
    attempts: int


def expected(
    product: str, state: DurableWorkState, *, accepted: tuple[str, ...] = ("a", "b"), attempts: int = 1
) -> EvidenceExpectation[BatchObservation]:
    return EvidenceExpectation(
        observation=BatchObservation(product=product, work_state=state, attempts=attempts),
        effects={key: 1 for key in accepted},
    )


@contextmanager
def receipts(*, retry_subset: bool = False) -> Iterator[EvidenceSession[BatchObservation]]:
    # ARRANGE: a real admitted two-member batch, isolated for each generated history.
    with (
        AsyncSingleThreadContext(),
        frozen_scheduler_clock() as clock,
        harness_owned_background_work(clock) as background,
    ):
        background.hold()
        work = record(batch, ("a", "b"))
        owner_id = work.owner_id
        visible: set[str] = set()
        original_attempt = None
        accepted = ("a",) if retry_subset else ("a", "b")
        expectations = {
            frozenset(): expected("unknown", DurableWorkState.UNKNOWN, accepted=accepted),
            frozenset({"receipt-a"}): expected("a", DurableWorkState.UNKNOWN, accepted=accepted),
            frozenset({"receipt-b"}): expected(
                "unknown" if retry_subset else "b", DurableWorkState.UNKNOWN, accepted=accepted
            ),
            frozenset({"receipt-a", "receipt-b"}): expected(
                "retry" if retry_subset else "a,b",
                DurableWorkState.RETRYABLE if retry_subset else DurableWorkState.SUCCEEDED,
                accepted=accepted,
            ),
        }

        def prepare() -> None:
            nonlocal original_attempt
            if retry_subset:
                BatchOperation.script.append(
                    BulkResult(items=(*result("a").items, *result("b", state=BulkOutcomeState.RETRYABLE).items))
                )
            # EXTERNAL SEAM: acceptance occurs in the independent provider before its reply is lost.
            with patch.object(BatchOperation, "lose_reply", True), pytest.raises(EffectOutcomeUnknownError):
                batch.attempt_execution(work.pk)
            original_attempt = attempts_for(work).get()

        def consume() -> None:
            # REAL PRODUCTION: record exact-attempt evidence and use registered Due recovery.
            assert original_attempt is not None
            evidence = BulkResult(
                items=tuple(
                    item
                    for key in sorted(visible)
                    for item in result(
                        key,
                        state=BulkOutcomeState.RETRYABLE if retry_subset and key == "b" else BulkOutcomeState.COMPLETE,
                    ).items
                )
            )
            batch.record_applied_effect_evidence(original_attempt, evidence)
            # EXTERNAL SEAM: this webhook delivery was consumed. The next
            # delivery can contain only a different partial receipt.
            visible.clear()
            batch.recover()

        def observe() -> BatchObservation:
            # OBSERVE: product publication, work state and the persisted attempt census.
            work.refresh_from_db()
            return BatchObservation(
                product=PrimitiveOwner.objects.get(pk=owner_id).value,
                # Reservation changes transport ownership, not the rejected
                # subset's outcome. No new provider attempt exists until retry.
                work_state=DurableWorkState.RETRYABLE if work.state == DurableWorkState.QUEUED else work.state,
                attempts=attempts_for(work).count(),
            )

        def retry(replay):
            original = BatchOperation.perform_effect

            def before_provider(operation, current):
                replay()  # The new EffectAttempt is already committed.
                return original(operation, current)

            make_due(work)
            with patch.object(BatchOperation, "perform_effect", before_provider):
                batch.recover()
                background.release()
                background.hold()

        def settled_evidence():
            assert original_attempt is not None
            receipt = BatchReceipt.objects.get(attempt=original_attempt)
            return json.dumps({"members": receipt.members, "result": receipt.result}, sort_keys=True)

        try:
            yield EvidenceSession(
                prepare=prepare,
                facts={
                    "receipt-a": EvidenceArrival(publish=partial(visible.add, "a"), consume=consume),
                    "receipt-b": EvidenceArrival(publish=partial(visible.add, "b"), consume=consume),
                },
                observe=observe,
                expectations=expectations,
                retry=EvidenceRetry(
                    send=retry,
                    expectations={
                        **expectations,
                        frozenset({"receipt-a", "receipt-b"}): expected("a,b", DurableWorkState.SUCCEEDED, attempts=2),
                    },
                )
                if retry_subset
                else None,
                settled_evidence=settled_evidence if retry_subset else None,
                recover=batch.recover,
                advance=background.advance,
                effects=lambda: Counter(BatchOperation.accepted_by_work.get(work.pk, [])),
                actor_scope=connection_scope,
            )
        finally:
            BatchReceipt.objects.filter(attempt__in=attempts_for(work)).delete()
            attempts_for(work).delete()
            work.delete()
            PrimitiveOwner.objects.filter(pk=owner_id).delete()


BULK_RECEIPTS = EvidenceConfluence(
    name="bulk-partial-receipts",
    bind=receipts,
    facts=("receipt-a", "receipt-b"),
    batchable=True,
    ordered_pair=("receipt-a", "receipt-b"),
    no_retry_because="Both members were accepted; partial receipt delivery must settle without another provider call.",
)


BULK_RETRY_RECEIPTS = EvidenceConfluence(
    name="bulk-receipts-across-subset-retry",
    bind=partial(receipts, retry_subset=True),
    facts=("receipt-a", "receipt-b"),
    batchable=True,
    ordered_pair=("receipt-a", "receipt-b"),
    retry_turnover=True,
)
