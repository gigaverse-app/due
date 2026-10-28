"""Generated obligation histories run against the UUID business-row capability."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from functools import partial
from unittest.mock import patch

from pytest_obligation.contract import NotApplicable
from pytest_obligation.profiles.catalog import ConvergenceFamily
from pytest_obligation.profiles.eventual_convergence import ConvergentWrite

from due.bulk import BulkResult, merge_bulk_evidence
from due.django.conformance.regressions import EffectRegression
from due.django.conformance.suite import (
    DeliveryExample,
    EffectExamples,
    FailureExample,
    RejectionExample,
    TerminalExample,
)
from due.django.runtime.coordination import WorkFailureDisposition
from due.django.testing import make_due
from due.errors import EffectNotAppliedError, EffectRejection
from tests.harness.bulk import ItemResult, result
from tests.harness.bulk_histories import (
    BULK_RECEIPTS,
    BULK_RETRY_RECEIPTS,
    BulkHistoryHost,
    inline_receipt_snapshot,
    receipts,
    separate_receipt_snapshot,
)
from tests.harness.self_work import ChunkFields, SendChunk


def self_work_examples(capability: SendChunk, fixture: str) -> EffectExamples:
    def make_work(keys: tuple[str, ...] = ("a", "b", "c")) -> ChunkFields:
        # ARRANGE: admit through the ordinary business ORM insert.
        work = capability.model.objects.create(universe="acme", bulk_keys=list(keys))
        # ARRANGE: PostgreSQL defaults use the real clock; the generated tests use virtual time.
        make_due(work)
        return work

    def delivery(expected: str = "a,b,c") -> DeliveryExample[ChunkFields, str]:
        # ARRANGE: one real admitted batch; provider calls remain independently observable.
        work = make_work()
        return DeliveryExample(
            work,
            # OBSERVE: product state, not a coordination-state proxy.
            observe=lambda: capability.model.objects.get(pk=work.pk).product_status,
            expected=expected,
            effect_calls=lambda: sum(pk == work.pk for pk, _ in SendChunk.calls),
        )

    @contextmanager
    def retry() -> Iterator[FailureExample[ChunkFields]]:
        # EXTERNAL SEAM: the scripted provider definitively rejects every crossing.
        with patch.object(SendChunk, "failure", EffectNotAppliedError("definite rejection")):
            yield FailureExample(attempts=lambda work: sum(pk == work.pk for pk, _ in SendChunk.calls))

    def terminal() -> TerminalExample[ChunkFields]:
        # OBSERVE: exhaustion is mirrored onto the business status field.
        return TerminalExample(
            observe=lambda work: capability.model.objects.get(pk=work.pk).product_status,
            no_rearm_because="A fixed non-repeatable cohort cannot grant a fresh budget by admitting it again.",
        )

    def applied_receipt(work: ChunkFields) -> BulkResult[ItemResult]:
        # ARRANGE: exact provider evidence for the admitted members, without any production transition.
        return result(*work.bulk_keys)

    @contextmanager
    def rejection() -> Iterator[RejectionExample[ChunkFields, str]]:
        # EXTERNAL SEAM: a provider-level rejection proves none of this call's members applied.
        decision = EffectRejection(message="permanent provider rejection", disposition=WorkFailureDisposition.TERMINAL)
        with patch.object(SendChunk, "failure", EffectNotAppliedError(decision)):
            yield RejectionExample(delivery("terminal"), disposition=WorkFailureDisposition.TERMINAL)

    @contextmanager
    def provider_wait() -> Iterator[RejectionExample[ChunkFields, str]]:
        wait = timedelta(hours=1)
        decision = EffectRejection(message="429", retry_after=wait)
        with patch.object(SendChunk, "failure", EffectNotAppliedError(decision)):
            yield RejectionExample(delivery("retry"), disposition=WorkFailureDisposition.RETRYABLE, provider_wait=wait)

    @contextmanager
    def uncertainty() -> Iterator[DeliveryExample[ChunkFields, str]]:
        # EXTERNAL SEAM: the reply is lost; the provider's outcome cannot authorize a resend.
        with patch.object(SendChunk, "failure", TimeoutError("reply lost")):
            yield delivery("unknown")

    def convergence() -> ConvergentWrite:
        # ARRANGE: exact-attempt receipt values before and after evidence, using the public schema.
        # EXTERNAL SEAM: one definite member receipt supplied by the provider.
        def apply(current: BulkResult[ItemResult]) -> BulkResult[ItemResult]:
            # REAL PRODUCTION: the canonical store uses this same monotonic merge.
            return merge_bulk_evidence(("a",), current, result("a"))

        return ConvergentWrite(
            name="bulk exact-attempt receipt",
            settled_state=lambda: result("a"),
            unsettled_state=lambda: BulkResult[ItemResult](items=()),
            apply_evidence=apply,
            # OBSERVE: the receipt now covers the full captured membership definitively.
            is_settled=lambda current: bool(current.items) and current.complete,
        )

    CHUNK_HOST = BulkHistoryHost(
        capability=capability,
        make=partial(make_work, ("a", "b")),
        product=lambda work: capability.model.objects.get(pk=work.pk).product_status,
        cleanup_owner=lambda work: None,  # Deleting work already deletes the sole business row.
        receipt_snapshot=inline_receipt_snapshot if capability.receipt_model is None else separate_receipt_snapshot,
    )
    CHUNK_RECEIPTS = BULK_RECEIPTS.model_copy(update={"bind": partial(receipts, host=CHUNK_HOST)})
    CHUNK_RETRY_RECEIPTS = BULK_RETRY_RECEIPTS.model_copy(
        update={"bind": partial(receipts, host=CHUNK_HOST, retry_subset=True)}
    )

    return EffectExamples(
        regressions=(
            EffectRegression.UNCERTAINTY_SETTLEMENT,
            EffectRegression.DUPLICATE_EFFECT,
            EffectRegression.BULK_PROGRESS,
        ),
        make_work=make_work,
        delivery=delivery,
        retry=retry,
        terminal=terminal,
        applied_receipt=applied_receipt,
        receipt_repair=delivery,
        rejections={"provider-rejected": rejection, "provider-wait": provider_wait},
        uncertainties={"reply-lost": uncertainty},
        convergence=convergence,
        evidence_confluence={scenario.name: scenario for scenario in (CHUNK_RECEIPTS, CHUNK_RETRY_RECEIPTS)},
        convergence_families={
            ConvergenceFamily.STALE_SNAPSHOTS: NotApplicable(
                "A fixed batch refuses new inputs; no desired revision can supersede its captured membership."
            ),
            ConvergenceFamily.IN_FLIGHT: NotApplicable(
                "This immutable cohort has no competing desired-state writer; receipt actors are covered by evidence confluence."
            ),
        },
        admission_not_applicable="The producer inserts business intent and coordination in one row; the rollback control proves they cannot split.",
        fixtures=(fixture,),
    )
