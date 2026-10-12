"""
A selected proof cannot pass as a skip or expected failure; only unassessed declarations may xfail.

Design and proof boundaries: docs/architecture.md#verification.
Each child session is a fresh interpreter; see the harness module for why proofs share one.
"""

import pytest

from integration_tests.harness.collection_sessions import (
    OBSERVER_SUITE,
    ChildRun,
    ChildSession,
)
from integration_tests.harness.collection_sessions import (
    child_fixture as child_fixture,
)

# Each selected enrollment check gets a different runtime outcome injected after
# collection, bypassing the static mark inventory just as an autouse fixture or
# plugin could. The three suites are independent, so one session proves all three.
RUNTIME_FAULTS = {
    "TestRoom": "pytest.skip('injected outcome')",
    "TestPicture": "pytest.xfail('injected outcome')",
    "TestObserver": "request.node.add_marker(pytest.mark.xfail(reason='injected expected failure'))",
}


@pytest.fixture(scope="module")
def fault_run(tmp_path_factory: pytest.TempPathFactory) -> ChildRun:
    child = ChildSession(tmp_path_factory.mktemp("faults"))
    child.append("test_effects.py", OBSERVER_SUITE)
    actions = "".join(
        f"    if request.cls.__name__ == {suite!r}:\n        {action}\n" for suite, action in RUNTIME_FAULTS.items()
    )
    child.append(
        "conftest.py", f"\nimport pytest\n@pytest.fixture(autouse=True)\ndef runtime_fault(request):\n{actions}"
    )
    # One session serves three faults, so its exit code alone cannot show that
    # each converted report reached pytest's failure counter; print the counter.
    child.append(
        "conftest.py",
        "\ndef pytest_sessionfinish(session):\n    print(f'SESSION-TESTSFAILED={session.testsfailed}')\n",
    )
    return child.run(
        "-q",
        *(f"test_effects.py::{suite}::test_registered_effect_enrollment" for suite in RUNTIME_FAULTS),
        "--durable-work-report=effect-report.json",
        "--durable-observation-report=observations.json",
    )


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("suite", "capability"),
    [("TestRoom", "primitive_self_test"), ("TestPicture", "other_self_test"), ("TestObserver", "observer")],
    ids=["runtime-skip", "runtime-expected-failure", "unexpected-pass"],
)
def test_selected_proof_cannot_succeed_as_an_expected_failure_or_skip(
    fault_run: ChildRun, suite: str, capability: str
) -> None:
    assert fault_run.result.ret == pytest.ExitCode.TESTS_FAILED
    fault_run.result.stdout.fnmatch_lines(
        [f"*{suite}::test_registered_effect_enrollment*cannot be skipped or xfailed*"]
    )
    evidence = fault_run.report("effect-report.json")[capability]
    assert evidence["outcomes"] == {"test_registered_effect_enrollment": ["failed"]}

    execution = fault_run.report("observations.json")[f"test_effects.py::{suite}::test_registered_effect_enrollment"][0]
    assert "failed" in execution["outcomes"].values()
    assert execution["kind"] == "generated"


@pytest.mark.no_db
def test_selected_faults_leave_no_other_outcome(fault_run: ChildRun) -> None:
    # The fixture-time skip and xfail fail in setup (pytest counts them as errors);
    # the injected xfail mark lets the call pass, and that unexpected pass fails.
    fault_run.assert_outcomes(failed=1, errors=2)
    # Every converted report counts as a session failure, not only the first one.
    fault_run.result.stdout.fnmatch_lines([f"*SESSION-TESTSFAILED={len(RUNTIME_FAULTS)}*"])


@pytest.mark.no_db
@pytest.mark.parametrize("fault", [None, "skip", "unexpected-pass", "error"])
def test_only_the_unassessed_declaration_failure_is_expected(child: ChildSession, fault: str | None) -> None:
    """Assessment debt may xfail; setup skips, a repaired placeholder and unrelated errors cannot hide behind it."""
    if fault == "skip":
        child.append(
            "conftest.py",
            "\nimport pytest\n@pytest.fixture(autouse=True)\ndef interrupt(): pytest.skip('unrelated fixture skip')\n",
        )
    elif fault is not None:
        action = "return None" if fault == "unexpected-pass" else "raise ValueError('unrelated declaration error')"
        child.append(
            "test_effects.py",
            f"\ndef substituted_run(self, case):\n    {action}\nTestRoom.test_due_work_contract.__code__ = substituted_run.__code__\n",
        )
    result = child.run(
        "-q",
        "-rx",
        "test_effects.py::TestRoom::test_due_work_contract[I-not_assessed]",
        "--durable-work-report=effect-report.json",
        "--due-work-profile-report=profiles.json",
    )
    if fault is None:
        assert result.result.ret == pytest.ExitCode.OK
        result.assert_outcomes(xfailed=1)
        assert "Not assessed" in result.result.stdout.str()
        assert "NotApplicable" in result.result.stdout.str()
        profile = next(iter(result.report("profiles.json").values()))["profiles"]["I"]
        assert profile["assessment"]["state"] == "not assessed"
        assert not profile["verified"]
        assert result.report("effect-report.json")["primitive_self_test"]["outcomes"] == {
            "test_due_work_contract[I-not_assessed]": ["xfail"]
        }
    else:
        assert result.result.ret == pytest.ExitCode.TESTS_FAILED
        if fault == "unexpected-pass":
            assert "XPASS(strict)" in result.result.stdout.str()
