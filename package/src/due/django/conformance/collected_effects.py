"""
What an enrolled work-table suite records about its own collection and proofs.

The root guard (``harness/session/durable_suites.py``) only finds enrolled classes and calls the
hooks ``effect_contract_suite`` installs on them. The report shape and the skip/xfail policy live
here, imported by enrolled suites and the optional reporting plugin only, so changing them selects
those suites rather than every test.
"""

import inspect
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TypedDict

import pytest
from pytest_obligation.collection import assert_collected_proofs as assert_collected_proofs


class SourceLocation(TypedDict):
    """Navigation to an existing declaration/assertion, not another coverage authority."""

    callable: str
    path: str | None
    line: int | None


type ExampleSources = dict[str, SourceLocation | dict[str, SourceLocation] | None]


def source_location(binding: Callable[..., object], root: Path) -> SourceLocation:
    # Introspection is deliberately confined to this reporting boundary. Never
    # invoke factories or serialize partial arguments (which may contain secrets).
    while isinstance(binding, partial):
        binding = binding.func
    function = inspect.unwrap(binding)
    name = f"{getattr(function, '__module__', type(function).__module__)}.{getattr(function, '__qualname__', type(function).__qualname__)}"
    try:
        filename = inspect.getsourcefile(function)
        _, line = inspect.getsourcelines(function)
    except OSError, TypeError:
        filename, line = None, None
    path = Path(filename).resolve() if filename else None
    if path is not None and path.is_relative_to(root):
        path = path.relative_to(root)
    return SourceLocation(callable=name, path=path.as_posix() if path else None, line=line)


class CollectedEffect(TypedDict):
    capability: str
    suite: str
    contract: str
    production_policy: dict[str, str]
    required_by: dict[str, str]
    expected: list[str]
    # Exact declaration rows allowed to report assessment debt as expected failures.
    unassessed: list[str]
    collected: list[str]
    selected: list[str]
    outcomes: dict[str, list[str]]
    bindings: ExampleSources
    proof_sources: dict[str, SourceLocation]


REPORT = pytest.StashKey[dict[str, CollectedEffect]]()
NAMES = pytest.StashKey[dict[str, str]]()
UNASSESSED = pytest.StashKey[set[str]]()


def record_collected(
    config: pytest.Config, name: str, report: CollectedEffect, collected: list[pytest.Function]
) -> str:
    """Keep one suite's collection report for its proofs' outcomes; return the suite the root guard checks."""
    reports = config.stash.setdefault(REPORT, {})
    assert name not in reports, f"{name}: duplicate collected effect suite"
    reports[name] = report
    config.stash.setdefault(NAMES, {}).update({item.nodeid: name for item in collected})
    # Only declaration rows identified by the suite's canonical validator may xfail in their test body.
    config.stash.setdefault(UNASSESSED, set()).update(
        item.nodeid for item in collected if item.name in report["unassessed"]
    )
    return report["suite"]


def is_assessment_debt(item: pytest.Item, report: pytest.TestReport) -> bool:
    # Pytest adds this optional attribute for XFAIL/XPASS; it is absent on ordinary reports.
    return (
        item.nodeid in item.config.stash.get(UNASSESSED, set())
        and report.when == "call"
        and report.skipped
        and getattr(report, "wasxfail", None) is not None
    )


def enforce_proof_outcome(item: pytest.Item, report: pytest.TestReport) -> None:
    """A selected registered-effect proof may not pass by being skipped or xfailed."""
    if item.nodeid not in item.config.stash.get(NAMES, {}):
        return
    was_xfail = getattr(report, "wasxfail", None) is not None
    if (was_xfail or report.skipped) and not is_assessment_debt(item, report):
        report.outcome = "failed"
        report.longrepr = f"{item.nodeid}: a selected registered-effect proof cannot be skipped or xfailed"
        # pytest's session failure counter ignores reports carrying this
        # dynamic plugin attribute, even when their outcome says failed.
        if was_xfail:
            del report.wasxfail
