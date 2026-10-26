"""The scalar external-operation contract also runs with native UUID identity."""

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

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
from tests.harness.operation import ProviderResult
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.uuid_work import UUIDPublication, UUIDWork, admit, registered_uuid, uuid_capability
from tests.harness.uuid_work import uuid_publication as uuid_publication


def make_work() -> UUIDWork:
    return admit(uuid_capability)


def delivery(expected: str = "published") -> DeliveryExample[UUIDWork, str]:
    work = make_work()
    return DeliveryExample(
        work,
        observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
        expected=expected,
        effect_calls=lambda: UUIDPublication.calls.count(work.pk),
    )


@contextmanager
def retry() -> Iterator[FailureExample[UUIDWork]]:
    with patch.object(UUIDPublication, "failure", EffectNotAppliedError("definite refusal")):
        yield FailureExample(attempts=lambda work: UUIDPublication.calls.count(work.pk))


def terminal() -> TerminalExample[UUIDWork]:
    return TerminalExample(
        observe=lambda work: PrimitiveOwner.objects.get(pk=work.owner_id).value,
        no_rearm_because="This publication is one immutable provider occurrence; a new publication needs a new owner.",
    )


@contextmanager
def rejection() -> Iterator[RejectionExample[UUIDWork, str]]:
    error = EffectNotAppliedError(
        EffectRejection(message="permanent refusal", disposition=WorkFailureDisposition.TERMINAL)
    )
    with patch.object(UUIDPublication, "failure", error):
        yield RejectionExample(delivery("terminal"), disposition=WorkFailureDisposition.TERMINAL)


@contextmanager
def uncertainty() -> Iterator[DeliveryExample[UUIDWork, str]]:
    with patch.object(UUIDPublication, "lose_reply", True):
        yield delivery("unknown")


with registered_uuid():

    @pytest.mark.usefixtures("uuid_publication")
    @effect_contract_suite(
        uuid_capability,
        examples=EffectExamples(
            make_work=make_work,
            delivery=delivery,
            retry=retry,
            terminal=terminal,
            applied_receipt=lambda work: ProviderResult(value=work.payload),
            receipt_repair=delivery,
            rejections={"provider-refused": rejection},
            uncertainties={"accepted-reply-lost": uncertainty},
            immutable_inputs="The publication captures its payload once; changing owner fields cannot revise that immutable occurrence.",
            admission_not_applicable="The owner already exists; admission commits one new work row atomically.",
            fixtures=("uuid_publication",),
        ),
    )
    class TestUUIDOperationContract:
        pass
