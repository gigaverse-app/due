"""
Effect and observation reports from executed child sessions: selection, origin and worker merges.

Design and proof boundaries: docs/architecture.md#verification.
Each child session is a fresh interpreter; see the harness module for why proofs share one.
"""

import json
from pathlib import Path

import pytest

from integration_tests.harness.collection_sessions import (
    ChildRun,
    ChildSession,
    render_observation_report,
)

OBSERVED = """
    import pytest
    from pytest_obligation.evidence.observation import assert_observation

    def test_application():
        assert_observation({"event_id": "private-event"}, {"event_id": "private-event"}, because="published identity")

    def test_mismatch():
        assert_observation({"cache_control": "private-value"}, {"cache_control": "no-store"}, because="storage policy")

    @pytest.fixture
    def broken_teardown():
        yield
        raise RuntimeError("teardown broke")

    def test_teardown(broken_teardown):
        assert_observation({"objects": 1}, {"objects": 1}, because="one object")

    def test_skip():
        assert_observation({"event_id": "private-event"}, {"event_id": "private-event"}, because="before skip")
        pytest.skip("remaining checks unavailable")

    @pytest.mark.xfail(reason="known mismatch", strict=False)
    def test_xfail():
        assert_observation({"event_id": "wrong"}, {"event_id": "right"}, because="known mismatch")

    @pytest.mark.xfail(reason="unexpectedly repaired", strict=False)
    def test_xpass():
        assert_observation({"event_id": "right"}, {"event_id": "right"}, because="unexpected repair")

    @pytest.mark.xfail(reason="strict unexpected repair", strict=True)
    def test_strict_xpass():
        assert_observation({"event_id": "right"}, {"event_id": "right"}, because="strict repair")

    def test_unselected():
        assert False
"""


# One executed selection, in process and across two xdist workers: an explicit
# node id (TestRoom), a class narrowed by -k (TestPicture), an unrelated broken
# enrolled source under gigaverse/, and the observation specimens.
@pytest.fixture(scope="module", params=[0, 2])
def selected_run(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> ChildRun:
    child = ChildSession(tmp_path_factory.mktemp(f"selected-{request.param}"))
    child.edit(
        "test_effects.py",
        "producers={'declared': unused}",
        "producers={'registration': __import__('functools').partial(unused)}",
    )
    child.write("gigaverse/test_unselected.py", "effect_contract_suite !!! invalid syntax\n")
    child.write("test_observed.py", OBSERVED)
    return child.run(
        "-q",
        "-n",
        str(request.param),
        "test_effects.py::TestRoom::test_registered_effect_enrollment",
        "test_effects.py::TestPicture",
        "test_observed.py",
        "-k",
        "registered_effect_enrollment or (test_observed and not unselected)",
        "--durable-work-report=effect-report.json",
        "--durable-observation-report=observations.json",
    )


@pytest.mark.no_db
def test_selected_session_runs_exactly_the_selected_cases(selected_run: ChildRun) -> None:
    # Two enrollment checks plus the observation specimens (2 passed, 2 failed, 1 teardown error, ...).
    selected_run.assert_outcomes(passed=4, failed=2, errors=1, skipped=1, xfailed=1, xpassed=1)


@pytest.mark.no_db
def test_explicit_test_report_preserves_unexecuted_checks(selected_run: ChildRun) -> None:
    evidence = selected_run.report("effect-report.json")["primitive_self_test"]
    assert len(evidence["expected"]) > 1
    assert evidence["selected"] == ["test_registered_effect_enrollment"]
    assert evidence["outcomes"] == {"test_registered_effect_enrollment": ["passed"]}


@pytest.mark.no_db
def test_unselected_broken_enrollment_does_not_break_an_unrelated_selection(selected_run: ChildRun) -> None:
    """The inventory check owns unselected source; the plugin owns selected proofs."""
    stdout = selected_run.result.stdout.str()
    assert selected_run.result.ret == pytest.ExitCode.TESTS_FAILED, "only the failing observation specimens fail"
    assert "ERROR collecting" not in stdout and "invalid syntax" not in stdout
    assert selected_run.report("effect-report.json")["primitive_self_test"]["outcomes"] == {
        "test_registered_effect_enrollment": ["passed"]
    }


@pytest.mark.no_db
def test_report_locates_bindings_and_proofs_without_executing_examples(selected_run: ChildRun) -> None:
    # TestPicture was selected by class and narrowed by -k, so its unselected proofs were collected.
    evidence = selected_run.report("effect-report.json")["other_self_test"]
    assert evidence["outcomes"] == {"test_registered_effect_enrollment": ["passed"]}
    source = (selected_run.path / "test_effects.py").read_text()
    expected_line = source[: source.index("def unused():")].count("\n") + 1
    location = evidence["bindings"]["delivery"]
    assert location == {"callable": "test_effects.unused", "path": "test_effects.py", "line": expected_line}
    assert evidence["bindings"]["producers"] == {"registration": location}
    assert evidence["bindings"]["admissions"] == {}
    assert evidence["bindings"]["receipt_repair"] is None
    proof = evidence["proof_sources"]["test_due_work_contract[REPLAY_SAFE_EXECUTION-assert_replay_converges]"]
    assert proof["path"].endswith("/pytest_obligation/profiles/harmless_replay.py")
    assert proof["callable"].endswith(".assert_replay_converges")
    migrated_indexes = evidence["proof_sources"]["test_migrated_work_indexes"]
    assert migrated_indexes["path"].endswith("/due/django/conformance/_internal/suite.py")
    assert migrated_indexes["callable"].endswith(".test_migrated_work_indexes")
    assert "test_migrated_work_indexes" in evidence["expected"]
    assert "test_migrated_work_indexes" in evidence["collected"]
    assert "test_migrated_work_indexes" not in evidence["selected"]
    assert "test_migrated_work_indexes" not in evidence["outcomes"]
    assert "proves atomic enrollment" not in evidence["contract"]


@pytest.mark.no_db
def test_observation_report_records_executed_checks_without_values(selected_run: ChildRun) -> None:
    report = selected_run.path / "observations.json"
    raw = report.read_text()
    assert "private-event" not in raw and "private-value" not in raw and "no-store" not in raw
    evidence = json.loads(raw)
    assert "test_observed.py::test_unselected" not in evidence
    passed = evidence["test_observed.py::test_application"][0]
    assert passed["outcomes"] == {"setup": "passed", "call": "passed", "teardown": "passed"}
    check = passed["checks"][0]
    assert check["fields"] == ["event_id"] and check["matched"]
    assert check["because"] == "published identity"
    assert check["path"] == "test_observed.py" and check["line"] > 0
    assert not evidence["test_observed.py::test_mismatch"][0]["checks"][0]["matched"]
    assert evidence["test_observed.py::test_teardown"][0]["outcomes"]["teardown"] == "failed"
    rendered = render_observation_report(report)
    assert "test_teardown | application | failed" in rendered
    for name, status in (("skip", "skipped"), ("xfail", "xfail"), ("xpass", "xpass"), ("strict_xpass", "failed")):
        assert f"test_{name} | application | {status}" in rendered
    assert "private-event" not in rendered
    filtered = render_observation_report(report, "--field", "cache_control")
    assert "test_mismatch | application | failed" in filtered
    assert "test_application |" not in filtered


@pytest.mark.no_db
def test_parallel_report_rejects_different_production_policy() -> None:
    from due.django.conformance.collected_effects import CollectedEffect
    from due.django.conformance.reporting import merge_worker_reports

    def report(queue: str) -> CollectedEffect:
        return CollectedEffect(
            capability="production.effect",
            suite="domain.TestEffect",
            contract="A/B",
            production_policy={"queue": queue},
            bindings={},
            proof_sources={},
            unassessed=[],
            required_by={"A": "registered recovery"},
            expected=["first"],
            collected=["first"],
            selected=["first"],
            outcomes={"first": ["passed"]},
        )

    with pytest.raises(AssertionError, match="production_policy"):
        merge_worker_reports({"effect": report("celery")}, {"effect": report("another-queue")})


@pytest.mark.no_db
def test_unavailable_source_is_reported_without_inventing_a_location(tmp_path: Path) -> None:
    from due.django.conformance.collected_effects import source_location

    assert source_location(len, tmp_path) == {"callable": "builtins.len", "path": None, "line": None}


# Observation origin and per-test attribution: two independent specimen files, one session.
@pytest.fixture(scope="module")
def origin_run(tmp_path_factory: pytest.TempPathFactory) -> ChildRun:
    child = ChildSession(tmp_path_factory.mktemp("origin"))
    child.write(
        "test_async.py",
        """
        import pytest
        from pytest_obligation.evidence.observation import assert_observation

        @pytest.mark.asyncio(loop_scope="module")
        async def test_first():
            assert_observation({"first": 1}, {"first": 1}, because="first test")

        @pytest.mark.asyncio(loop_scope="module")
        async def test_second():
            assert_observation({"second": 2}, {"second": 2}, because="second test")
        """,
    )
    child.write(
        "test_native.py",
        """
        from pytest_obligation.contract import (
            NotApplicable, SafetyContract, Profile, safety_contract_suite,
        )
        from pytest_obligation.evidence.observation import assert_observation

        @safety_contract_suite(SafetyContract(
            name="report origin", profiles={profile: NotApplicable("metadata control") for profile in (Profile.H, Profile.J)},
        ))
        class TestGenerated:
            pass

        class TestInherited(TestGenerated):
            pass

        class TestOverride(TestGenerated):
            def test_safety_contract(self):
                assert_observation({"identity": 1}, {"identity": 1}, because="handwritten replacement")

        class TestSameName:
            def test_safety_contract(self):
                assert_observation({"identity": 1}, {"identity": 1}, because="handwritten same name")
        """,
    )
    # The overridden specimen deliberately removes generated coverage. Select
    # that single observation-origin probe explicitly; a whole-suite run must
    # now reject it (obligation's collection controls test that rejection).
    return child.run(
        "test_async.py",
        "test_native.py::TestGenerated",
        "test_native.py::TestInherited",
        "test_native.py::TestOverride::test_safety_contract",
        "test_native.py::TestSameName",
        "-q",
        "--durable-observation-report=observations.json",
    )


@pytest.mark.no_db
def test_origin_specimens_all_pass(origin_run: ChildRun) -> None:
    # test_async.py: 2, test_native.py: 6.
    origin_run.assert_outcomes(passed=8)


@pytest.mark.no_db
def test_async_observations_belong_to_the_current_test(origin_run: ChildRun) -> None:
    evidence = origin_run.report("observations.json")
    for name in ("first", "second"):
        assert [check["fields"] for check in evidence[f"test_async.py::test_{name}"][0]["checks"]] == [[name]]


@pytest.mark.no_db
def test_native_observation_origin_follows_the_generated_method(origin_run: ChildRun) -> None:
    evidence = {
        nodeid: executions
        for nodeid, executions in origin_run.report("observations.json").items()
        if nodeid.startswith("test_native.py::")
    }
    assert len(evidence) == 6
    for nodeid, executions in evidence.items():
        expected = "application" if "TestOverride" in nodeid or "TestSameName" in nodeid else "generated"
        assert executions[0]["kind"] == expected, nodeid
    rendered = render_observation_report(origin_run.path / "observations.json")
    assert "TestOverride::test_safety_contract | application | passed" in rendered


# Both repeated-delivery histories in one `--dist=each` session: every test runs
# on both workers, so each nodeid carries two execution histories to merge.
REPEATED_HISTORIES = {False: "test_every_worker_passes", True: "test_one_worker_fails"}


@pytest.fixture(scope="module")
def repeated_run(tmp_path_factory: pytest.TempPathFactory) -> ChildRun:
    child = ChildSession(tmp_path_factory.mktemp("repeated"))
    child.write(
        "test_repeated.py",
        """
        from pytest_obligation.evidence.observation import assert_observation

        def test_every_worker_passes(worker_id):
            assert_observation({"delivery": 1}, {"delivery": 1}, because="worker delivery")
            assert worker_id in {"gw0", "gw1"}

        def test_one_worker_fails(worker_id):
            assert_observation({"delivery": 1}, {"delivery": 1}, because="worker delivery")
            assert worker_id == "gw0", "deliberate failure in one worker"
        """,
    )
    return child.run(
        "test_repeated.py", "-q", "-n", "2", "--dist=each", "--durable-observation-report=observations.json"
    )


@pytest.mark.no_db
def test_repeated_worker_session_outcomes(repeated_run: ChildRun) -> None:
    repeated_run.assert_outcomes(passed=3, failed=1)


@pytest.mark.no_db
@pytest.mark.parametrize("fail_one_worker", [False, True])
def test_repeated_worker_runs_preserve_both_outcomes(repeated_run: ChildRun, fail_one_worker: bool) -> None:
    nodeid = f"test_repeated.py::{REPEATED_HISTORIES[fail_one_worker]}"
    evidence = repeated_run.report("observations.json")[nodeid]
    # Execution histories must not overwrite one another or crash the report merge.
    assert len(evidence) == 2
    assert sorted(execution["outcomes"]["call"] for execution in evidence) == (
        ["failed", "passed"] if fail_one_worker else ["passed", "passed"]
    )
    assert all(len(execution["checks"]) == 1 for execution in evidence)

    rendered = render_observation_report(repeated_run.path / "observations.json")
    assert rendered.count(f"{nodeid} | application | passed") == (1 if fail_one_worker else 2)
    assert rendered.count(f"{nodeid} | application | failed") == int(fail_one_worker)
