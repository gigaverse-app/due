"""
Root controls for the discovery admissibility invariants.

Each invariant must fail on the defect it names and pass on the repair, using
the real derive() of a test-owned capability: a candidate query that selects
blank rows the work rejects, one that selects rows desired_for declines, and a
page loop that stops at its first failing owner.
"""

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import timedelta
from typing import ClassVar
from unittest.mock import patch

import pytest
from django.db import connection, models
from django.db.models import QuerySet
from django.test.utils import isolate_apps
from pytest_obligation.integrations.django.edge_values import CandidateEdge
from pytest_obligation.integrations.django.owed_rows import EdgeFamily

from due.django.conformance.discovery_admissibility import (
    assert_database_legal_candidate_is_admissible,
    assert_inadmissible_candidate_does_not_block_the_next,
    candidate_edges,
    related_candidate_edges,
)
from due.django.conformance.examples import EffectExamples
from due.django.public import (
    AppliedEvidenceContract,
    Discovery,
    DurableWorkPolicy,
    ReplaySafeEffect,
    UniverseDurableWork,
    database_now,
    durable_work_schema,
)
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


# Related rows: a discovery that reads its owner's parent, whose legacy values perturbing the owner never reaches.

with isolate_apps("tests"):

    class ScopedParent(models.Model):
        label = models.CharField(max_length=40)

        class Meta:
            app_label = "tests"

    class ScopedOwner(models.Model):
        parent = models.ForeignKey(ScopedParent, on_delete=models.CASCADE)
        completed_at = models.DateTimeField(null=True)

        class Meta:
            app_label = "tests"

    class ScopedWork(UniverseDurableWork):
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(ScopedOwner, on_delete=models.SET_NULL, null=True)
        payload = models.CharField(max_length=40)

        class Meta:
            app_label = "tests"
            indexes, constraints = durable_work_schema(name_prefix="scoped_admissibility")


class ScopedEffect(ReplaySafeEffect[ScopedOwner, ScopedWork, str]):
    def perform_effect(self, work: ScopedWork) -> str:
        return work.payload


def _parent_label(owner: ScopedOwner, existing: ScopedWork | None) -> ScopedWork:
    # The parent's label feeds the work's required payload, as a parent's tenant feeds a work's scope.
    return ScopedWork(payload=owner.parent.label, universe="tests")


def _scoped(candidates: Callable[[], QuerySet[ScopedOwner]]) -> ScopedEffect:
    return ScopedEffect.for_owner(
        owner=ScopedOwner,
        work=ScopedWork,
        policy=DurableWorkPolicy(retry_delay=timedelta(0)),
        discovery=Discovery(
            candidates=candidates,
            desired_for=_parent_label,
            page_size=10,
            applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
            unbounded_because="Only owners created by this self-test are candidates.",
        ),
    )


def _owed_scoped() -> QuerySet[ScopedOwner]:
    return ScopedOwner.objects.filter(completed_at__isnull=True).order_by("pk")


def _owed_scoped_with_a_label() -> QuerySet[ScopedOwner]:
    # The repair: the candidate query excludes owners whose parent cannot fill the work.
    return _owed_scoped().exclude(parent__label__regex=r"^\s*$")


PARENT = EdgeFamily(model=ScopedParent, edge_row=lambda owner: owner.parent)
PARENT_BLANK = CandidateEdge(field="label", kind="blank", value="")


def _implied_with_its_own_parent() -> int:
    return ScopedOwner.objects.create(parent=ScopedParent.objects.create(label="tenant")).pk


_SHARED: list[ScopedParent] = []


def _implied_under_one_parent() -> int:
    if not _SHARED:
        _SHARED.append(ScopedParent.objects.create(label="tenant"))
    return ScopedOwner.objects.create(parent=_SHARED[0]).pk


@pytest.fixture(name="scoped_tables")
def scoped_tables_fixture(transactional_db: None) -> Iterator[None]:
    _SHARED.clear()
    with connection.schema_editor() as editor:
        for model in (ScopedParent, ScopedOwner, ScopedWork):
            editor.create_model(model)
    try:
        yield
    finally:
        with connection.schema_editor() as editor:
            for model in (ScopedWork, ScopedOwner, ScopedParent):
                editor.delete_model(model)


def test_related_edges_carry_their_family_name_and_cover_the_related_model() -> None:
    related = related_candidate_edges({"parent": PARENT})
    assert [edge.id for edge in related] == ["parent-label-blank", "parent-label-whitespace", "parent-label-max_length"]
    # NEGATIVE CONTROL: the owner's own edges never reach the parent's label, which is the gap families close.
    assert all(edge.field != "label" for edge in candidate_edges(_scoped(_owed_scoped)))


@pytest.mark.usefixtures("scoped_tables")
class TestRelatedRowEdges:
    def test_rejects_a_candidate_query_that_selects_owners_whose_parent_the_work_cannot_hold(self) -> None:
        with pytest.raises(AssertionError, match="related ScopedParent has label-blank .* cannot admit"):
            assert_database_legal_candidate_is_admissible(
                _scoped(_owed_scoped), _implied_with_its_own_parent, PARENT_BLANK, PARENT
            )

    def test_accepts_a_candidate_query_that_excludes_those_owners(self) -> None:
        assert_database_legal_candidate_is_admissible(
            _scoped(_owed_scoped_with_a_label), _implied_with_its_own_parent, PARENT_BLANK, PARENT
        )

    def test_rejects_a_page_loop_that_stops_at_the_owner_whose_parent_is_blank(self) -> None:
        with (
            patch.object(ScopedEffect, "derive", _page_aborting_derive),
            pytest.raises(AssertionError, match="related ScopedParent has label-blank .* stopped discovery"),
        ):
            assert_inadmissible_candidate_does_not_block_the_next(
                _scoped(_owed_scoped), _implied_with_its_own_parent, PARENT_BLANK, PARENT
            )

    def test_accepts_the_same_loop_when_the_query_excludes_that_owner(self) -> None:
        with patch.object(ScopedEffect, "derive", _page_aborting_derive):
            assert_inadmissible_candidate_does_not_block_the_next(
                _scoped(_owed_scoped_with_a_label), _implied_with_its_own_parent, PARENT_BLANK, PARENT
            )

    def test_refuses_owners_that_share_the_perturbed_parent(self) -> None:
        # One poisoned parent would poison both owners, so the case could not tell a blocking loop from two bad rows.
        with pytest.raises(AssertionError, match="both implied owners read the same related ScopedParent row"):
            assert_inadmissible_candidate_does_not_block_the_next(
                _scoped(_owed_scoped_with_a_label), _implied_under_one_parent, PARENT_BLANK, PARENT
            )

    def test_refuses_a_family_whose_row_is_not_its_model(self) -> None:
        wrong = EdgeFamily(model=PrimitiveOwner, edge_row=lambda owner: owner.parent)
        with pytest.raises(AssertionError, match="PrimitiveOwner fields, but they would be written into ScopedParent"):
            assert_database_legal_candidate_is_admissible(
                _scoped(_owed_scoped), _implied_with_its_own_parent, BLANK, wrong
            )


@pytest.mark.parametrize(
    ("families", "derivation", "refusal"),
    [
        ({"parent": EdgeFamily(model=ScopedParent)}, True, "declares no edge_row"),
        ({" ": PARENT}, True, "nonempty case-id fragment"),
        ({"parent": PARENT}, False, "declare derivation"),
    ],
    ids=["owner-itself", "blank-name", "no-derivation"],
)
def test_examples_refuse_a_family_the_cases_cannot_run(
    families: dict[str, EdgeFamily], derivation: bool, refusal: str
) -> None:
    unused = pytest.fail
    with pytest.raises(ValueError, match=refusal):
        EffectExamples(
            make_work=unused, delivery=unused, derivation=unused if derivation else None, discovery_edges=families
        )
