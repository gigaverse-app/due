"""Executable boundary checks for the normalized, source-proved runtime core."""

import os
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from due import claim_authority, retry_delays
from due.claim_authority import callback_admissions, claim_authorized
from due.django.models import DurableWorkState
from due.django.runtime.coordination import WorkClaim, _UncommittedClaim, normalized_authority
from tests.harness.primitive import PrimitiveWork
from tests.harness.self_work import InlineChunk

pytestmark = pytest.mark.no_db


def test_imports_current_production_source():
    root = Path(__file__).resolve().parents[2] / "src/due"
    # The mutation runner intentionally supplies a staged current source tree.
    # Its separate import attestation validates that location before pytest.
    expected = Path(os.environ.get("DUE_CONTRACT_SOURCE", root)).resolve()
    assert Path(claim_authority.__file__).resolve() == expected / "claim_authority.py"
    assert Path(retry_delays.__file__).resolve() == expected / "retry_delays.py"


@pytest.mark.parametrize("uuid_key", [False, True], ids=["int-key", "uuid-key"])
@pytest.mark.parametrize("fused", [False, True], ids=["committed", "fused"])
@pytest.mark.parametrize("changed", ["none", "pk", "state", "token", "revision"])
def test_normalization_preserves_the_full_ownership_predicate(uuid_key, fused, changed):
    model = InlineChunk if uuid_key else PrimitiveWork
    identity = uuid4() if uuid_key else 7
    token, stored_token = uuid4(), uuid4()
    claim = WorkClaim(
        work=model(pk=identity),
        token=token,
        claim_generation=9,
        desired_revision=3,
        uncommitted=_UncommittedClaim(DurableWorkState.READY, stored_token, 2) if fused else None,
    )
    fields = claim._authority()
    current = model(**fields)
    if changed == "pk":
        if uuid_key:
            cast(InlineChunk, current).pk = uuid4()
        else:
            cast(PrimitiveWork, current).pk = 8
    elif changed == "state":
        current.state = DurableWorkState.UNKNOWN
    elif changed == "token":
        current.fencing_token = uuid4()
    elif changed == "revision":
        current.desired_revision += 1
    # Independent original predicate and ORM lookup values must agree with the
    # pure core on both ordinary and uncommitted stored claim fences.
    original = type(current) is claim.model and all(getattr(current, name) == value for name, value in fields.items())
    assert claim.owns_locked(current) is original is (changed == "none")


def test_wrong_model_rejects_before_reading_any_fields():
    claim = WorkClaim(InlineChunk(), uuid4(), 1, 1)
    assert claim.owns_locked(cast(InlineChunk, object())) is False


@pytest.mark.parametrize("field", ["pk", "state", "fencing_token", "desired_revision"])
def test_corrupt_current_fence_rejects_without_coercion_or_exception(field):
    claim = WorkClaim(InlineChunk(), uuid4(), 1, 1)
    current = InlineChunk(**claim._authority())
    setattr(current, field, None)
    assert claim.owns_locked(current) is False


def test_scalar_enum_and_uuid_normalization_preserves_disjoint_identity_domains():
    token = uuid4()
    key = UUID(int=7)
    integer = normalized_authority(7, "claimed", token, 1)
    uuid = normalized_authority(key, DurableWorkState.CLAIMED, token, 1)
    assert not claim_authorized(True, integer, uuid)
    assert uuid == normalized_authority(UUID(str(key)), "claimed", UUID(str(token)), 1)
    for identity in (True, "7"):
        with pytest.raises(TypeError, match="identity"):
            normalized_authority(cast(int, identity), "claimed", token, 1)


def test_callback_admissions_preserve_presence_model_identity_and_order():
    captured = normalized_authority(7, "claimed", uuid4(), 1)
    stale = normalized_authority(7, "claimed", uuid4(), 2)
    assert callback_admissions(
        [captured] * 4, [captured, stale, captured, captured], [True, True, False, True], [True, True, True, False]
    ) == [True, False, False, False]


@pytest.mark.parametrize("initial,maximum", [(1, 1), (3, 10), (7, 9), (1, 86_399_999_999_999_999_999)])
def test_delay_matches_exact_saturating_microsecond_arithmetic(initial, maximum):
    delay = retry_delays.ExponentialDelay(timedelta(microseconds=initial), timedelta(microseconds=maximum))
    previous = timedelta(0)
    for step in (0, 1, 2, 3, 10, 40, 80, 10_000):
        expected = timedelta(microseconds=min(initial * 2**step, maximum))
        assert previous <= delay.at(step) == expected <= delay.maximum
        previous = expected
    with pytest.raises(ValueError):
        delay.at(-1)


@pytest.mark.parametrize("initial,maximum", [(0, 1), (-1, 1), (2, 1)])
def test_delay_rejects_invalid_configuration(initial, maximum):
    with pytest.raises(ValueError):
        retry_delays.ExponentialDelay(timedelta(microseconds=initial), timedelta(microseconds=maximum))
