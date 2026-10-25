"""Generated obligation histories run against the real replay-safe bulk capability."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_obligation.profiles.harmless_replay import ReplaySafeEffect

from due.django.conformance.suite import (
    CheckpointedReplay,
    DeliveryExample,
    EffectExamples,
    FailureExample,
    ProducerExample,
    RejectionExample,
    TerminalExample,
    effect_contract_suite,
)
from due.errors import EffectNotAppliedError, EffectRejection, WorkFailureDisposition
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.replay_safe_bulk import (
    MemberSync,
    MemberSyncWork,
    admit_cohort,
    member_sync,
    record_sync,
    registered_member_sync,
)
from tests.harness.replay_safe_bulk import member_sync_environment as member_sync_environment


def make_work() -> MemberSyncWork:
    # ARRANGE: admit through the actual capability's producer transaction.
    return record_sync()


def delivery() -> DeliveryExample[MemberSyncWork, str]:
    work = make_work()
    return DeliveryExample(
        work,
        # OBSERVE: product state and the provider's own member set, not coordination state.
        observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
        expected="a,b,c",
        checkpoint=CheckpointedReplay(
            because="Each round commits its member verdicts under the fence before claim settlement; a replay sends only members still without a verdict.",
            applied=lambda row: not member_sync.pending_keys(row),
        ),
        effect_calls=lambda: len(MemberSync.calls),
    )


@contextmanager
def retry() -> Iterator[FailureExample[MemberSyncWork]]:
    # EXTERNAL SEAM: every call fails before the provider answers.
    with patch.object(MemberSync, "script", [TimeoutError("connection reset")] * 20):
        yield FailureExample(attempts=lambda work: len(MemberSync.calls))


@contextmanager
def provider_wait() -> Iterator[RejectionExample[MemberSyncWork, str]]:
    wait = timedelta(hours=1)
    error = EffectNotAppliedError(EffectRejection(message="429", retry_after=wait))
    with patch.object(MemberSync, "script", [error] * 20):
        work = make_work()
        yield RejectionExample(
            DeliveryExample(
                work,
                observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
                expected="before",
                effect_calls=lambda: len(MemberSync.calls),
            ),
            disposition=WorkFailureDisposition.RETRYABLE,
            provider_wait=wait,
        )


@contextmanager
def replay() -> Iterator[ReplaySafeEffect]:
    yield ReplaySafeEffect(
        name="add a fixed member cohort",
        # ARRANGE: one admitted cohort through the real admission transaction.
        prepare=make_work,
        # REAL PRODUCTION: the adopter's single provider call for exactly that cohort.
        execute=lambda work: member_sync.send_batch(work, tuple(work.bulk_keys), None),
        # EXTERNAL SEAM: the scripted provider keeps its own idempotent member set.
        # OBSERVE: the provider's member set after one and two calls.
        observe=lambda work: sorted(MemberSync.members),
        expected=["a", "b", "c"],
        execution_count_for=lambda work: sum(keys == tuple(work.bulk_keys) for keys in MemberSync.calls),
    )


@contextmanager
def producer() -> Iterator[ProducerExample]:
    # ARRANGE: one owner whose cohort a repeated sync request must not replace.
    owner = PrimitiveOwner.objects.create()
    # REAL PRODUCTION: the cohort's admission transaction.
    yield ProducerExample(
        produce=lambda: (admit_cohort(owner.pk),),
        observe=lambda: MemberSyncWork.objects.exists(),
        expected=True,
        effect_calls=lambda: len(MemberSync.calls),
    )


def terminal() -> TerminalExample[MemberSyncWork]:
    return TerminalExample(
        observe=lambda work: PrimitiveOwner.objects.get(pk=work.owner_id).value,
        no_rearm_because="A fixed cohort is admitted once; a new sync admits a new cohort.",
    )


with registered_member_sync():

    @pytest.mark.usefixtures("member_sync_operation")
    @effect_contract_suite(
        member_sync,
        examples=EffectExamples(
            make_work=make_work,
            delivery=delivery,
            retry=retry,
            rejections={"provider-wait": provider_wait},
            terminal=terminal,
            replay=replay,
            admissions={"cohort": producer},
            perturb_inputs=lambda work: PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="moved"),
            immutable_inputs="A cohort's member keys are captured at admission; a later sync admits a new cohort.",
            admission_not_applicable="The owner exists before admission; admission commits one new work row atomically.",
            fixtures=("member_sync_operation",),
        ),
    )
    class TestReplaySafeBulkContract:
        pass
