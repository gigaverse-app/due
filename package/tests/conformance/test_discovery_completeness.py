"""Real derivation must not enroll an owner that unbounded discovery can never find."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

import pytest
from pytest_obligation.integrations.django.selection_completeness import (
    CompletenessBinding,
    SelectionIncomplete,
    assert_selection_is_complete,
)

from due.django.conformance.discovery_admissibility import discovery_completeness
from due.django.public import AppliedEvidenceContract, Discovery
from due.django.testing import hold_nudges
from tests.harness.primitive import PrimitiveOwner, PrimitiveWork, primitive
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = pytest.mark.usefixtures("isolated_primitive")


@pytest.mark.parametrize("misses_branch", [False, True], ids=["complete", "missing"])
def test_unbounded_discovery_selects_every_owner_it_would_enroll(misses_branch: bool) -> None:
    def desired(owner: PrimitiveOwner, existing: PrimitiveWork | None) -> PrimitiveWork | None:
        if owner.value == "valid" or (misses_branch and owner.value.strip()):
            return PrimitiveWork(payload=owner.value, universe="tests")
        return None

    capability = replace(
        primitive,
        discovery=Discovery(
            candidates=lambda: PrimitiveOwner.objects.filter(value="valid"),
            desired_for=desired,
            page_size=10,
            applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
            unbounded_because="Every eligible owner in this test belongs to this discovery.",
        ),
    )

    @contextmanager
    def enter() -> Iterator[CompletenessBinding]:
        with hold_nudges():
            yield discovery_completeness(capability, lambda: PrimitiveOwner.objects.create(value="valid").pk)

    if misses_branch:
        with pytest.raises(SelectionIncomplete, match="missing an owed branch"):
            assert_selection_is_complete(enter)
    else:
        verdicts = assert_selection_is_complete(enter)
        assert verdicts["selected"] and verdicts["skipped"]
