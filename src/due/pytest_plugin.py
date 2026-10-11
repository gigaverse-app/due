"""
Reject a selected durable-work suite that collection lost, and dispatch to the suites that survived.

An enrolled suite (``effect_contract_suite``) can disappear before its module imports, through
``collect_ignore`` or a constructor, and then nothing of it runs to notice. Only an
invocation-wide hook can compare the selected source inventory with what was collected. The
suites own everything else through the hooks ``effect_contract_suite`` installs on the class:

* ``__durable_work_collect__(config, items) -> str`` validates and records the suite's collected
  proofs, and returns the suite's dotted name;
* ``__durable_work_outcome__(item, report)`` applies the suite's outcome policy to each report.

This plugin imports no suite, report or application code, so its dependents are every test while
the policy it dispatches to is selected only with the suites that use it.
"""

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

# pytest-global: Detect selected suites omitted by collection rules even when their module never imports; suite-local checks cannot see an omitted module. The scanner imports no application code.
from due._suite_inventory import selected_enrollment

type CollectSuite = Callable[[pytest.Config, list[pytest.Function]], str]
type EnforceOutcome = Callable[[pytest.Item, pytest.TestReport], None]


def requires_complete_suite(arguments: list[str], module: Path, class_name: str) -> bool:
    """A broad selector still requires the whole suite beside any narrow selectors."""
    for argument in arguments:
        path, *nodes = argument.split("::")
        selected_path = Path(path).resolve()
        if selected_path == module or selected_path in module.parents:
            if not nodes or nodes == [class_name]:
                return True
    return False


def _enrolled(item: pytest.Item) -> type | None:
    cls = item.cls if isinstance(item, pytest.Function) else None
    return cls if getattr(cls, "__durable_work_capability__", None) is not None else None


# pytest-global: A suite removed by collect_ignore never imports its validator; only an invocation-wide hook can compare selected source enrollment with actual collection and reject that silent loss.
@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]):
    classes: dict[type, list[pytest.Function]] = {}
    for item in items:
        if (cls := _enrolled(item)) is not None:
            classes.setdefault(cls, []).append(cast(pytest.Function, item))
    collected_suites = set()
    for cls, collected in classes.items():
        validator = getattr(cls, "__durable_work_collect__", None)
        assert callable(validator), f"{cls.__name__}: enrolled suite lost its collection validator"
        assert callable(getattr(cls, "__durable_work_outcome__", None)), (
            f"{cls.__name__}: enrolled suite lost its outcome policy"
        )
        collected_suites.add(cast(CollectSuite, validator)(config, collected))
    root = config.rootpath
    if root.is_dir():
        # The complete repository inventory is enforced by test_conformance_enrollment.
        # This hook owns only selected paths: changing an unrelated test must not
        # make every invocation depend on the entire repository's test contents.
        for enrollment in selected_enrollment(root, config.args):
            if requires_complete_suite(config.args, enrollment.module, enrollment.class_name):
                module = ".".join(enrollment.module.relative_to(config.rootpath).with_suffix("").parts)
                assert f"{module}.{enrollment.class_name}" in collected_suites, (
                    f"{enrollment.capability_path}: selected enrolled suite never collected"
                )
    yield


# pytest-global: Skipped and xfailed proofs can bypass suite-local fixtures and test bodies; the mandatory invocation hook must hand those outcomes to the suite's policy even without the optional reporting plugin.
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item):
    outcome = yield
    if (cls := _enrolled(item)) is not None:
        cast(EnforceOutcome, cls.__durable_work_outcome__)(item, outcome.get_result())
