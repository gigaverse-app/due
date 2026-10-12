"""Explicit reporting plugin: load with -p before requesting report options."""

import json
from pathlib import Path
from typing import Any

import pytest
from pytest_obligation.evidence.observation import (
    ObservationOutcome,
    ObservationReport,
    ObservedTest,
    record_observations,
)

from due.django.conformance.collected_effects import (
    NAMES,
    REPORT,
    CollectedEffect,
    is_assessment_debt,
)

_OBSERVATIONS = pytest.StashKey[ObservationReport]()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "durable_work_proof: method installed by the shared contract generator")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--durable-observation-report", help="Write executed observation names and test outcomes as JSON")
    parser.addoption("--durable-work-report", help="Write collected effect contracts and actual outcomes as JSON")


def merge_worker_reports(target: dict[str, CollectedEffect], incoming: dict[str, CollectedEffect]) -> None:
    for name, report in incoming.items():
        if name not in target:
            target[name] = report
            continue
        current = target[name]
        # Outcomes from different contracts/policies cannot certify one capability.
        for field in (
            "capability",
            "suite",
            "contract",
            "production_policy",
            "required_by",
            "expected",
            "unassessed",
            "bindings",
        ):
            assert current[field] == report[field], f"{name}: workers disagree on {field}"
        for proof, location in report["proof_sources"].items():
            assert current["proof_sources"].setdefault(proof, location) == location, (
                f"{name}: workers disagree on proof source {proof}"
            )
        for field in ("collected", "selected"):
            current[field] = sorted(set(current[field]) | set(report[field]))
        for proof, outcomes in report["outcomes"].items():
            current["outcomes"].setdefault(proof, []).extend(outcomes)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: object) -> None:
    # pytest-xdist owns this dynamic plugin boundary; workers return their real
    # outcomes to the controller, which is the only process writing the report.
    reports = node.config.stash.setdefault(REPORT, {})
    merge_worker_reports(reports, node.workeroutput.get("durable_work_report", {}))
    observations = node.config.stash.setdefault(_OBSERVATIONS, {})
    if incoming := node.workeroutput.get("durable_observation_report"):
        from pytest_obligation.evidence.observation_report import REPORT_ADAPTER

        for nodeid, executions in REPORT_ADAPTER.validate_json(incoming).items():
            observations.setdefault(nodeid, []).extend(executions)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item):
    if not item.config.getoption("durable_observation_report"):
        yield
        return
    with record_observations() as checks:
        yield
    item.config.stash[_OBSERVATIONS][item.nodeid][-1].checks.extend(checks)


@pytest.hookimpl(hookwrapper=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]):
    yield
    # Other plugins / -k can select a subset. Report it as partial, never as full proof.
    names = config.stash.get(NAMES, {})
    for item in items:
        if (name := names.get(item.nodeid)) is not None:
            config.stash[REPORT][name]["selected"].append(item.name)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item):
    # Run after the mandatory proof check has normalized skipped/xfail outcomes.
    outcome = yield
    report: pytest.TestReport = outcome.get_result()
    was_xfail = getattr(report, "wasxfail", None) is not None
    if (name := item.config.stash.get(NAMES, {}).get(item.nodeid)) is not None and (
        report.when == "call" or report.failed
    ):
        item.config.stash[REPORT][name]["outcomes"].setdefault(item.name, []).append(
            "xfail" if is_assessment_debt(item, report) else report.outcome
        )
    if item.config.getoption("durable_observation_report"):
        tests = item.config.stash.setdefault(_OBSERVATIONS, {})
        executions = tests.setdefault(item.nodeid, [])
        if report.when == "setup":
            name = item.config.stash.get(NAMES, {}).get(item.nodeid)
            registered = name is not None and item.name in item.config.stash[REPORT][name]["expected"]
            native = item.get_closest_marker("due_work") is not None
            executions.append(ObservedTest(kind="generated" if registered or native else "application"))
        observed = executions[-1]
        # pytest attaches wasxfail to both expected failures and non-strict
        # unexpected passes; preserve that distinction for coverage decisions.
        status: ObservationOutcome = report.outcome
        if was_xfail:
            status = "xpass" if report.passed else "xfail"
        observed.outcomes[report.when] = status


def pytest_sessionfinish(session: pytest.Session) -> None:
    reports = session.config.stash.get(REPORT, {})
    worker_output = getattr(session.config, "workeroutput", None)
    if session.config.getoption("durable_observation_report"):
        from pytest_obligation.evidence.observation_report import report_json

        serialized = report_json(session.config.stash.get(_OBSERVATIONS, {}), session.config.rootpath)
        if worker_output is not None:
            worker_output["durable_observation_report"] = serialized
        else:
            path = Path(session.config.getoption("durable_observation_report"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(serialized)
    if worker_output is not None:
        worker_output["durable_work_report"] = reports
        return
    destination = session.config.getoption("durable_work_report")
    if destination:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
