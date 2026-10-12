"""
Self-test the generated lost-acknowledgement history against the real runtime.

The root primitive's effect is replay-safe: the worker dies after it returns,
inside settlement, and registered recovery must replay it to one settled
outcome. Each negative control changes one adopter seam the way a real
adopter gets it wrong, and the same generated assertion must reject it.
"""

from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import patch

import pytest

from due.django.capabilities.compensation import IndependentCompensation
from due.django.conformance.examples import DeliveryExample
from due.django.conformance.scenarios import (
    acknowledgement_replays,
    assert_lost_acknowledgement_replay_converges,
)
from due.django.public import DurableWorkPolicy, WorkClaim, WorkFailureDisposition
from due.django.testing import hold_nudges
from tests.harness import primitive as example
from tests.harness.operation import Publication, PublicationWork
from tests.harness.primitive import PrimitiveEffect, PrimitiveOwner, PrimitiveWork, primitive, record
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


#: The provider's own records, outside the database transaction a worker death rolls back.
PROVIDER: dict[int | None, list[str]] = {}


def delivery() -> DeliveryExample[PrimitiveWork, tuple[str, tuple[str, ...]]]:
    # ARRANGE: the real producer admits one owed effect.
    PROVIDER.clear()
    with hold_nudges():
        work = record()
    return DeliveryExample(
        work=work,
        # OBSERVE: the product row and the provider's records for this owner.
        observe=lambda: (PrimitiveOwner.objects.get(pk=work.owner_id).value, tuple(PROVIDER.get(work.owner_id, ()))),
        expected=("after", ("after",)),
        effect_calls=lambda: len(PrimitiveEffect.calls),
    )


def upserting_call(work: PrimitiveWork) -> str:
    # A replay-safe provider: the call sets the owner's record.
    PrimitiveEffect.calls.append(work.payload)
    PROVIDER[work.owner_id] = [work.payload]
    return work.payload


def test_replay_safe_effect_converges_and_settles() -> None:
    assert acknowledgement_replays(primitive)
    with patch.object(example, "call", upserting_call):
        assert_lost_acknowledgement_replay_converges(primitive, delivery())


def appending_call(work: PrimitiveWork) -> str:
    # A non-idempotent provider: each call adds another record.
    PrimitiveEffect.calls.append(work.payload)
    PROVIDER.setdefault(work.owner_id, []).append(work.payload)
    return work.payload


def conflicting_call(work: PrimitiveWork) -> str:
    # The provider refuses a second create; the adopter classifies that refusal as permanent.
    PrimitiveEffect.calls.append(work.payload)
    if work.owner_id in PROVIDER:
        raise FileExistsError("already exists")
    PROVIDER[work.owner_id] = [work.payload]
    return work.payload


def guarded_call(work: PrimitiveWork) -> str:
    # A local "already sent" guard: the redelivery never reaches the provider, so nothing is replayed.
    if work.owner_id not in PROVIDER:
        upserting_call(work)
    return work.payload


@pytest.mark.parametrize(
    ("call", "classification", "defect"),
    [
        (appending_call, None, "one logical effect"),
        (conflicting_call, WorkFailureDisposition.TERMINAL, "never settled"),
        (guarded_call, None, "Zero means the replay never ran"),
    ],
    ids=["duplicates-the-effect", "replay-classified-permanent", "replay-never-reaches-provider"],
)
def test_broken_replays_are_rejected(call, classification: WorkFailureDisposition | None, defect: str) -> None:
    with (
        patch.object(example, "call", call),
        patch.object(PrimitiveEffect, "classify_error", lambda self, error: classification)
        if classification
        else nullcontext(),
        pytest.raises(AssertionError, match=defect),
    ):
        assert_lost_acknowledgement_replay_converges(primitive, delivery())


class _Cleanup(IndependentCompensation[PrimitiveWork, PrimitiveWork]):
    def capture(self, producer: WorkClaim[PrimitiveWork]) -> PrimitiveWork:
        return PrimitiveWork(payload=producer.work.payload, universe="acme")

    def compensate(self, work: PrimitiveWork) -> bool | None:
        return None


@pytest.mark.parametrize(
    "capability",
    [
        Publication.for_owner(owner=PrimitiveOwner, work=PublicationWork, policy=DurableWorkPolicy(retention=None)),
        _Cleanup(
            model=PrimitiveWork,
            producer_model=PrimitiveWork,
            nudge=lambda identity, token: None,
            policy=DurableWorkPolicy(),
        ),
        replace(primitive, policy=DurableWorkPolicy(max_rearms=0)),
    ],
    ids=["evidence-required-operation", "independent-compensation", "zero-retry-budget"],
)
def test_history_is_generated_only_where_recovery_replays(capability) -> None:
    # Receipt repair owns operations; compensation is born claimed; budget 0 exhausts on the lost claim.
    assert not acknowledgement_replays(capability)
