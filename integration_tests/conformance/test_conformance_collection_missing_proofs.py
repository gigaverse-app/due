"""
Collection fails when a selected or enrolled suite loses any generated proof or its validator.

Design and proof boundaries: docs/architecture.md#verification.
Each child session is a fresh interpreter; see the harness module for why proofs share one.
"""

from pathlib import Path

import pytest

from integration_tests.harness.collection_sessions import (
    COLLECTION_PLUGIN,
    HANDOFF_CASE,
    ChildRun,
    ChildSession,
    add_room_eligibility,
    add_room_handoff,
    lost_proofs,
)
from integration_tests.harness.collection_sessions import (
    child_fixture as child_fixture,
)


@pytest.mark.no_db
@pytest.mark.parametrize("selection", ["sibling", "same_class", "whole_file"])
def test_explicit_test_selection_cannot_hide_missing_checks_in_a_fully_selected_suite(
    child: ChildSession, selection: str
) -> None:
    # The command-line selection is the proof, so each one keeps its own session.
    child.append("test_effects.py", "\ndel TestPicture.test_registered_producer_lost_notification\n")
    partial = "test_effects.py::TestRoom::test_registered_effect_enrollment"
    complete = {
        "sibling": "test_effects.py::TestPicture",
        "same_class": "test_effects.py::TestPicture",
        "whole_file": "test_effects.py",
    }[selection]
    if selection == "same_class":
        partial = "test_effects.py::TestPicture::test_registered_effect_enrollment"
    result = child.run("--collect-only", "-q", partial, complete).result
    assert result.ret != pytest.ExitCode.OK
    assert "test_registered_producer_lost_notification" in lost_proofs(result, "TestPicture")


# One collection of TestRoom with every required proof family removed at once.
# The hook reports a suite's complete missing set, so each family is checked in
# that one list. Another suite's failure would be masked by TestRoom's, so the
# observer and dropped-binding failures keep their own sessions (in
# test_conformance_collection_declared_scenarios.py). The two
# due-work-contract families delete the whole generated test_due_work_contract
# and each checks the cases its own declaration adds to it.
REMOVED_ROOM_PROOFS = {
    "admission": (
        "test_application_admission_transaction",
        [
            "test_application_admission_transaction[commit-register]",
            "test_application_admission_transaction[rollback-register]",
        ],
    ),
    "migrated-indexes": ("test_migrated_work_indexes", ["test_migrated_work_indexes"]),
    "standalone-admission-failure": (
        "test_due_work_contract",
        [
            "test_due_work_contract[I-expiry-assert_successful_admission_commits]",
            "test_due_work_contract[I-expiry-assert_interrupted_admission_rolls_back]",
        ],
    ),
    "due-work-contract-eligibility": (
        "test_due_work_contract",
        ["test_due_work_contract[eligibility-unsettled_dependency-assert_blocked_gate_preserves_intent]"],
    ),
    "due-work-contract-handoff": ("test_due_work_contract", [HANDOFF_CASE]),
}


@pytest.mark.no_db
def test_declared_sensitivity_control_cannot_disappear_from_collection(child: ChildSession) -> None:
    child.edit(
        "test_effects.py",
        "from due.django.conformance.suite import EffectExamples, effect_contract_suite",
        "from due.django.conformance.suite import EffectExamples, effect_contract_suite\n"
        "from due.django.conformance.regressions import EffectRegression",
    )
    child.edit(
        "test_effects.py",
        "examples = EffectExamples(",
        "examples = EffectExamples(regressions=(EffectRegression.LOST_PUBLICATION,),",
    )
    baseline = child.run("--collect-only", "-q", "test_effects.py::TestRoom").result
    assert baseline.ret == pytest.ExitCode.OK
    child.append("test_effects.py", "\ndel TestRoom.test_contract_detects_regression\n")
    broken = child.run("--collect-only", "-q", "test_effects.py::TestRoom").result
    assert broken.ret != pytest.ExitCode.OK
    assert "test_contract_detects_regression[disable-lost-publication-recovery]" in lost_proofs(broken, "TestRoom")


@pytest.fixture(scope="module")
def room_without_proofs(tmp_path_factory: pytest.TempPathFactory) -> ChildRun:
    child = ChildSession(tmp_path_factory.mktemp("room-without-proofs"))
    add_room_eligibility(child)
    add_room_handoff(child)
    child.edit(
        "test_effects.py",
        "examples = EffectExamples(",
        "examples = EffectExamples(admissions={'register': unused}, admission_failures={'expiry': unused},",
    )
    removed = dict.fromkeys(proof for proof, _ in REMOVED_ROOM_PROOFS.values())
    child.append("test_effects.py", "".join(f"\ndel TestRoom.{proof}" for proof in removed) + "\n")
    return child.run("--collect-only", "-q", "test_effects.py")


@pytest.mark.no_db
@pytest.mark.parametrize("family", list(REMOVED_ROOM_PROOFS))
def test_collection_requires_every_generated_proof_family(room_without_proofs: ChildRun, family: str) -> None:
    assert room_without_proofs.result.ret != pytest.ExitCode.OK
    _, required_cases = REMOVED_ROOM_PROOFS[family]
    missing = lost_proofs(room_without_proofs.result, "TestRoom")
    for case in required_cases:
        assert case in missing


@pytest.mark.no_db
def test_collection_requires_eligibility_proofs_per_declared_scenario(room_without_proofs: ChildRun) -> None:
    missing = lost_proofs(room_without_proofs.result, "TestRoom")
    for name in ("active_owner", "unsettled_dependency"):
        # The four gate proofs, plus the case tying the gate to the contract's own sweep.
        assert len([case for case in missing if f"eligibility-{name}-" in case]) == 5


@pytest.mark.no_db
def test_missing_suite_validator_fails_without_optional_reporting(tmp_path: Path) -> None:
    # Validator and ignored-module failures are raised by the same hook in order,
    # so each keeps its own session rather than masking the other.
    child = ChildSession(tmp_path, conftest=f"pytest_plugins = ['{COLLECTION_PLUGIN}']")
    child.append("test_effects.py", "\ndel TestRoom.__durable_work_collect__\n")
    result = child.run("--collect-only", "-q", "test_effects.py::TestRoom").result
    assert result.ret != pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["*TestRoom: enrolled suite lost its collection validator*"])


@pytest.mark.no_db
def test_ignored_enrolled_module_fails_without_importing_it(tmp_path: Path) -> None:
    child = ChildSession(
        tmp_path, conftest=f"pytest_plugins = ['{COLLECTION_PLUGIN}']\ncollect_ignore = ['gigaverse/test_missing.py']\n"
    )
    child.write(
        "gigaverse/test_missing.py",
        "from due.django.conformance.suite import effect_contract_suite\n"
        "from gigaverse.core.missing_capability import production\n"
        "raise AssertionError('ignored module must never import')\n"
        "@effect_contract_suite(production)\n"
        "class TestMissing: pass\n",
    )
    child.write("gigaverse/test_control.py", "def test_control(): pass\n")
    result = child.run("--collect-only", "-q", "gigaverse").result
    assert result.ret != pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["*selected enrolled suite never collected*"])
    assert "ModuleNotFoundError" not in result.stdout.str()


@pytest.mark.no_db
@pytest.mark.parametrize("remove_proof", [False, True], ids=["complete", "missing"])
def test_unbounded_discovery_cannot_lose_its_selection_proof(child: ChildSession, remove_proof: bool) -> None:
    child.edit(
        "effects.py",
        "room_storage_cleanup = primitive",
        """from due.django.public import Discovery, AppliedEvidenceContract
from tests.harness.primitive import PrimitiveOwner
room_storage_cleanup = replace(primitive, discovery=Discovery(
    candidates=lambda: PrimitiveOwner.objects.filter(value='valid'),
    desired_for=lambda owner, existing: None,
    page_size=10,
    applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
    unbounded_because='All pending owners are discoverable without a time horizon.',
))""",
    )
    child.edit(
        "test_effects.py",
        "immutable_inputs='An immutable captured cleanup identity.',",
        "snapshot=unused, derivation=unused,",
    )
    child.edit("effects.py", "replace(primitive, model=OtherWork)", "replace(room_storage_cleanup, model=OtherWork)")
    child.edit("test_effects.py", "perturb_inputs=unused,", "")
    if remove_proof:
        child.append("test_effects.py", "\ndel TestRoom.test_discovery_selection_is_complete\n")
    result = child.run("--collect-only", "-q", "test_effects.py::TestRoom").result
    if remove_proof:
        assert result.ret != pytest.ExitCode.OK
        assert "test_discovery_selection_is_complete" in lost_proofs(result, "TestRoom")
    else:
        assert result.ret == pytest.ExitCode.OK, result.stdout.str()
        assert "test_discovery_selection_is_complete" in result.stdout.str()


@pytest.mark.no_db
@pytest.mark.parametrize("remove_proof", [False, True], ids=["complete", "missing"])
def test_a_declared_related_row_family_cannot_lose_its_admissibility_cases(
    child: ChildSession, remove_proof: bool
) -> None:
    child.edit(
        "effects.py",
        "room_storage_cleanup = primitive",
        """from due.django.public import Discovery, AppliedEvidenceContract
from tests.harness.primitive import PrimitiveOwner
room_storage_cleanup = replace(primitive, discovery=Discovery(
    candidates=lambda: PrimitiveOwner.objects.filter(value='valid').order_by('pk'),
    desired_for=lambda owner, existing: None,
    page_size=10,
    applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
    unbounded_because='All pending owners are discoverable without a time horizon.',
))""",
    )
    child.edit(
        "test_effects.py",
        "immutable_inputs='An immutable captured cleanup identity.',",
        "snapshot=unused, derivation=unused, discovery_edges={'sibling': __import__("
        "'pytest_obligation.integrations.django.owed_rows', fromlist=['EdgeFamily']).EdgeFamily("
        "model=__import__('tests.harness.primitive', fromlist=['PrimitiveOwner']).PrimitiveOwner, "
        "edge_row=lambda owner: owner)},",
    )
    child.edit("effects.py", "replace(primitive, model=OtherWork)", "replace(room_storage_cleanup, model=OtherWork)")
    child.edit("test_effects.py", "perturb_inputs=unused,", "")
    case = "test_database_legal_candidate_is_admissible[sibling-value-blank]"
    if remove_proof:
        child.append("test_effects.py", "\ndel TestRoom.test_database_legal_candidate_is_admissible\n")
    result = child.run("--collect-only", "-q", "test_effects.py::TestRoom").result
    if remove_proof:
        assert result.ret != pytest.ExitCode.OK
        assert case in lost_proofs(result, "TestRoom")
    else:
        assert result.ret == pytest.ExitCode.OK, result.stdout.str()
        assert case in result.stdout.str()
