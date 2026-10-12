"""
Declared eligibility scenarios, handoffs and observer histories are reported and required at collection.

Design and proof boundaries: docs/architecture.md#verification.
Each child session is a fresh interpreter; see the harness module for why proofs share one.
"""

import pytest

from integration_tests.harness.collection_sessions import (
    HANDOFF_CASE,
    OBSERVER_SUITE,
    ChildRun,
    ChildSession,
    add_discovery_and_inline_caller,
    add_room_eligibility,
    add_room_handoff,
    lost_proofs,
)
from integration_tests.harness.collection_sessions import (
    child_fixture as child_fixture,
)

CALLER_DISCOVERY_PROOFS = {
    "test_discovery_selection_is_complete",
    *(
        f"test_synchronous_caller[{history}-cleanup]"
        for history in (
            "inline",
            "enclosing-commit",
            "enclosing-rollback",
            "effect-failure",
            "claim-held",
            "repeat-same-value",
            "response-lost",
        )
    ),
}


@pytest.mark.no_db
@pytest.mark.parametrize("remove_cases", [False, True], ids=["reported", "missing"])
def test_caller_histories_and_discovery_proofs_are_required_and_reported(
    child: ChildSession, remove_cases: bool
) -> None:
    add_discovery_and_inline_caller(child)
    if remove_cases:
        child.append(
            "test_effects.py",
            "del TestRoom.test_synchronous_caller\ndel TestRoom.test_discovery_selection_is_complete\n",
        )
    result = child.run("--collect-only", "-q", "test_effects.py::TestRoom", "--durable-work-report=effect-report.json")
    if remove_cases:
        assert result.result.ret != pytest.ExitCode.OK
        assert set(lost_proofs(result.result, "TestRoom")) == CALLER_DISCOVERY_PROOFS
    else:
        assert result.result.ret == pytest.ExitCode.OK, result.result.stdout.str()
        entry = result.report("effect-report.json")["primitive_self_test"]
        assert CALLER_DISCOVERY_PROOFS <= set(entry["expected"])
        assert CALLER_DISCOVERY_PROOFS <= set(entry["collected"])
        assert entry["outcomes"] == {}, "collection must not claim executed proofs"


# One successful collection of TestRoom with eligibility scenarios and of an
# observer suite. Collection is navigation, not execution evidence.
@pytest.fixture(scope="module")
def complete_collection(tmp_path_factory: pytest.TempPathFactory) -> ChildRun:
    child = ChildSession(tmp_path_factory.mktemp("complete"))
    add_room_eligibility(child)
    add_room_handoff(child)
    child.append("test_effects.py", OBSERVER_SUITE)
    return child.run("--collect-only", "-q", "test_effects.py", "--durable-work-report=effect-report.json")


@pytest.mark.no_db
def test_complete_collection_succeeds(complete_collection: ChildRun) -> None:
    assert complete_collection.result.ret == pytest.ExitCode.OK, complete_collection.result.stdout.str()


@pytest.mark.no_db
def test_observer_collection_reports_generated_histories(complete_collection: ChildRun) -> None:
    # Every generated case is required and linked to its binding; outcomes remain empty.
    entry = complete_collection.report("effect-report.json")["observer"]
    for history in ("inconclusive", "lost_delivery", "competing_receipt"):
        case = f"test_observation_recovers_without_resend[{history}]"
        assert case in entry["expected"] and case in entry["collected"]
    assert entry["bindings"]["observation"]["callable"].endswith(".unused")
    assert entry["outcomes"] == {}


@pytest.mark.no_db
def test_named_eligibility_histories_are_reported(complete_collection: ChildRun) -> None:
    data = complete_collection.report("effect-report.json")["primitive_self_test"]
    assert data["production_policy"]["eligibility_scenarios"] == "active_owner, unsettled_dependency"
    for name in ("active_owner", "unsettled_dependency"):
        cases = {case for case in data["expected"] if f"eligibility-{name}-" in case}
        # The four gate proofs, plus the case tying the gate to the contract's own sweep.
        assert len(cases) == 5
        assert any(case.endswith("-assert_gate_is_recovered_by_the_contract_sweep]") for case in cases)
        assert cases <= set(data["collected"])
        assert data["bindings"]["eligibility"][name]["callable"].endswith(".unused")
    assert data["outcomes"] == {}, "collection must not masquerade as executed evidence"


@pytest.mark.no_db
def test_observer_collection_requires_generated_histories(child: ChildSession) -> None:
    child.append("test_effects.py", OBSERVER_SUITE + "\ndel TestObserver.test_observation_recovers_without_resend\n")
    result = child.run("--collect-only", "-q", "test_effects.py::TestObserver").result
    assert result.ret != pytest.ExitCode.OK
    assert lost_proofs(result, "TestObserver") == [
        f"test_observation_recovers_without_resend[{history}]"
        for history in ("competing_receipt", "inconclusive", "lost_delivery")
    ]


@pytest.mark.no_db
def test_dropped_eligibility_binding_fails_collection(child: ChildSession) -> None:
    add_room_eligibility(child)
    child.append(
        "test_effects.py",
        "\ncontract = TestRoom.__due_work_contract__\n"
        "TestRoom.__due_work_contract__ = contract.model_copy(update={'eligibility': {'active_owner': contract.eligibility['active_owner']}})\n",
    )
    result = child.run("--collect-only", "-q", "test_effects.py::TestRoom").result
    assert result.ret != pytest.ExitCode.OK
    assert "eligibility variants differ from declared product scenarios" in result.stdout.str()


@pytest.mark.no_db
def test_work_table_handoff_histories_are_reported(complete_collection: ChildRun) -> None:
    data = complete_collection.report("effect-report.json")["primitive_self_test"]
    assert HANDOFF_CASE in data["expected"] and HANDOFF_CASE in data["collected"]
    assert data["bindings"]["handoffs"]["accepted-work"]["callable"].endswith(".attempt_execution")
    assert data["outcomes"] == {}, "collection cannot claim executed coverage"


@pytest.mark.no_db
def test_dropped_handoff_binding_fails_collection(child: ChildSession) -> None:
    add_room_handoff(child)
    child.append(
        "test_effects.py",
        "\nTestRoom.__due_work_contract__ = TestRoom.__due_work_contract__.model_copy(update={'handoffs': (), 'handoff_delivery': None})\n",
    )
    result = child.run("--collect-only", "-q", "test_effects.py::TestRoom").result
    assert result.ret != pytest.ExitCode.OK
    assert "handoff histories differ from declared product transitions" in result.stdout.str()
