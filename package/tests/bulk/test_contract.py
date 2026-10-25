"""Generated obligation histories run against the real bulk work-table capability."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_obligation.contract import NotApplicable
from pytest_obligation.profiles.catalog import ConvergenceFamily
from pytest_obligation.profiles.eventual_convergence import ConvergentWrite

from due.bulk import BulkResult, merge_bulk_evidence
from due.django.conformance.suite import (
    DeliveryExample,
    EffectExamples,
    FailureExample,
    RejectionExample,
    TerminalExample,
    effect_contract_suite,
)
from due.django.runtime.coordination import WorkFailureDisposition
from due.errors import EffectNotAppliedError, EffectRejection
from tests.harness.bulk import BatchOperation, BatchWork, ItemResult, batch, record, registered_batch, result
from tests.harness.bulk import batch_environment as batch_environment
from tests.harness.bulk_histories import BULK_RECEIPTS, BULK_RETRY_RECEIPTS
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture


def make_work() -> BatchWork:
    # ARRANGE: admit through the actual bulk capability's producer transaction.
    return record(batch)


def delivery(expected: str = "a,b,c") -> DeliveryExample[BatchWork, str]:
    # ARRANGE: one real admitted batch; provider calls remain independently observable.
    work = make_work()
    return DeliveryExample(
        work,
        # OBSERVE: product state, not a coordination-state proxy.
        observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
        expected=expected,
        effect_calls=lambda: sum(pk == work.pk for pk, _ in BatchOperation.requests),
    )


@contextmanager
def retry() -> Iterator[FailureExample[BatchWork]]:
    # EXTERNAL SEAM: the scripted provider definitively rejects every crossing.
    with patch.object(BatchOperation, "failure", EffectNotAppliedError("definite rejection")):
        yield FailureExample(attempts=lambda work: sum(pk == work.pk for pk, _ in BatchOperation.requests))


def terminal() -> TerminalExample[BatchWork]:
    # OBSERVE: exhaustion is mirrored onto the independent product owner.
    return TerminalExample(
        observe=lambda work: PrimitiveOwner.objects.get(pk=work.owner_id).value,
        no_rearm_because="A fixed non-repeatable cohort cannot grant a fresh budget by admitting it again.",
    )


def applied_receipt(work: BatchWork) -> BulkResult[ItemResult]:
    # ARRANGE: exact provider evidence for the admitted members, without any production transition.
    return result(*work.bulk_keys)


@contextmanager
def rejection() -> Iterator[RejectionExample[BatchWork, str]]:
    # EXTERNAL SEAM: a provider-level rejection proves none of this call's members applied.
    decision = EffectRejection(message="permanent provider rejection", disposition=WorkFailureDisposition.TERMINAL)
    with patch.object(BatchOperation, "failure", EffectNotAppliedError(decision)):
        yield RejectionExample(delivery("terminal"), disposition=WorkFailureDisposition.TERMINAL)


@contextmanager
def provider_wait() -> Iterator[RejectionExample[BatchWork, str]]:
    wait = timedelta(hours=1)
    decision = EffectRejection(message="429", retry_after=wait)
    with patch.object(BatchOperation, "failure", EffectNotAppliedError(decision)):
        yield RejectionExample(delivery("retry"), disposition=WorkFailureDisposition.RETRYABLE, provider_wait=wait)


@contextmanager
def uncertainty() -> Iterator[DeliveryExample[BatchWork, str]]:
    # EXTERNAL SEAM: the reply is lost; the provider's outcome cannot authorize a resend.
    with patch.object(BatchOperation, "failure", TimeoutError("reply lost")):
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


with registered_batch():

    @pytest.mark.usefixtures("batch_operation")
    @effect_contract_suite(
        batch,
        examples=EffectExamples(
            make_work=make_work,
            delivery=delivery,
            retry=retry,
            terminal=terminal,
            applied_receipt=applied_receipt,
            receipt_repair=delivery,
            rejections={"provider-rejected": rejection, "provider-wait": provider_wait},
            uncertainties={"reply-lost": uncertainty},
            convergence=convergence,
            evidence_confluence={scenario.name: scenario for scenario in (BULK_RECEIPTS, BULK_RETRY_RECEIPTS)},
            convergence_families={
                ConvergenceFamily.STALE_SNAPSHOTS: NotApplicable(
                    "A fixed batch refuses new inputs; no desired revision can supersede its captured membership."
                ),
                ConvergenceFamily.IN_FLIGHT: NotApplicable(
                    "This immutable cohort has no competing desired-state writer; receipt actors are covered by evidence confluence."
                ),
            },
            admission_not_applicable="The owner exists before admission; admission commits one new work row atomically.",
            fixtures=("batch_operation",),
        ),
    )
    class TestBulkOperationContract:
        pass
