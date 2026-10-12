"""
Root controls for the discovery admissibility invariants.

Each invariant must fail on the defect it names and pass on the repair, using
the real derive() of a test-owned capability: a candidate query that selects
blank rows the work rejects, one that selects rows desired_for declines, and a
page loop that stops at its first failing owner.
"""

from collections.abc import Callable
from dataclasses import replace
from unittest.mock import patch

import pytest
from django.db.models import QuerySet
from pytest_obligation.integrations.django.edge_values import CandidateEdge

from due.django.conformance.discovery_admissibility import (
    assert_database_legal_candidate_is_admissible,
    assert_inadmissible_candidate_does_not_block_the_next,
    candidate_edges,
)
from due.django.public import AppliedEvidenceContract, Discovery, database_now
from tests.harness.primitive import (
    PrimitiveEffect,
    PrimitiveOwner,
    PrimitiveWork,
    owed_owners,
    primitive,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

BLANK = CandidateEdge(field="value", kind="blank", value="")
WHITESPACE = CandidateEdge(field="value", kind="whitespace", value=" ")
LONGEST = CandidateEdge(field="value", kind="max_length", value="x" * 40)


def _copied_value(owner: PrimitiveOwner, existing: PrimitiveWork | None) -> PrimitiveWork:
    # The owner's value feeds the work's required payload, as picture.file.name feeds source_key.
    return PrimitiveWork(payload=owner.value, universe="tests")


def _declining_blank(owner: PrimitiveOwner, existing: PrimitiveWork | None) -> PrimitiveWork | None:
    return None if owner.value == "" else _copied_value(owner, existing)


def _discovering(
    candidates: QuerySet[PrimitiveOwner],
    desired_for: Callable[[PrimitiveOwner, PrimitiveWork | None], PrimitiveWork | None] = _copied_value,
) -> PrimitiveEffect:
    return replace(
        primitive,
        discovery=Discovery(
            candidates=lambda: candidates,
            desired_for=desired_for,
            page_size=10,
            applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
            unbounded_because="Only owners created by this self-test are candidates.",
        ),
    )


def _implied() -> int:
    return PrimitiveOwner.objects.create(value="valid").pk


def _page_aborting_derive(self: PrimitiveEffect) -> int:
    """DEFECTIVE: the pre-isolation page loop, which stops at its first failing owner."""
    assert self.discovery is not None
    page = self.discovery.bounded_candidates(now=database_now()).values_list("pk", flat=True)
    for owner_id in page[: self.discovery.page_size]:
        with self.atomic(owner_id, notify_worker=False) as effect:
            desired = self.discovery.desired_for(effect.locked_owner, effect.existing_work)
            if desired is not None:
                effect.request_work(desired)
    return 0


def test_capability_edges_cover_the_owner_fields_and_nothing_else() -> None:
    assert candidate_edges(primitive) == (BLANK, WHITESPACE, LONGEST)


@pytest.mark.usefixtures("isolated_primitive")
class TestDatabaseLegalCandidateIsAdmissible:
    def test_rejects_a_candidate_query_that_selects_rows_the_work_cannot_hold(self) -> None:
        # derive() raises the ValidationError itself, or a group containing it once the page isolates owners.
        with pytest.raises(AssertionError, match="value-blank .* cannot admit, and derive\\(\\) raised"):
            assert_database_legal_candidate_is_admissible(_discovering(owed_owners()), _implied, BLANK)

    def test_accepts_an_edge_value_the_work_holds(self) -> None:
        # POSITIVE CONTROL for the case above: same query, same derive(), a value the work accepts.
        assert_database_legal_candidate_is_admissible(_discovering(owed_owners()), _implied, LONGEST)

    def test_accepts_a_candidate_query_that_excludes_the_row(self) -> None:
        assert_database_legal_candidate_is_admissible(_discovering(owed_owners().exclude(value="")), _implied, BLANK)

    def test_rejects_a_candidate_that_desired_for_declines(self) -> None:
        capability = _discovering(owed_owners(), desired_for=_declining_blank)
        with pytest.raises(AssertionError, match="value-blank .* desired_for declines it"):
            assert_database_legal_candidate_is_admissible(capability, _implied, BLANK)


@pytest.mark.usefixtures("isolated_primitive")
class TestInadmissibleCandidateDoesNotBlockTheNext:
    def test_rejects_a_page_loop_that_stops_at_its_first_failure(self) -> None:
        with (
            patch.object(PrimitiveEffect, "derive", _page_aborting_derive),
            pytest.raises(AssertionError, match="value-blank .* first in candidate order stopped discovery"),
        ):
            assert_inadmissible_candidate_does_not_block_the_next(_discovering(owed_owners()), _implied, BLANK)

    def test_accepts_the_same_loop_when_the_query_excludes_the_row(self) -> None:
        with patch.object(PrimitiveEffect, "derive", _page_aborting_derive):
            assert_inadmissible_candidate_does_not_block_the_next(
                _discovering(owed_owners().exclude(value="")), _implied, BLANK
            )

    def test_accepts_an_edge_value_that_sorts_after_valid_owners(self) -> None:
        # Descending by value puts "" last: such a row never precedes a valid owner.
        with patch.object(PrimitiveEffect, "derive", _page_aborting_derive):
            assert_inadmissible_candidate_does_not_block_the_next(
                _discovering(owed_owners().order_by("-value", "pk")), _implied, BLANK
            )
