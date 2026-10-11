"""
Child pytest sessions that exercise the durable-work collection and report plugins.

Design and proof boundaries: docs/architecture.md#verification.

Why proofs share sessions: every child boots Django in a fresh interpreter, and an
xdist child pays it again in each worker. A child that collects the ``EFFECTS``
specimen also imports every registered adopter through the work-table suite: in
CI (October 2026) such a child took 7-10 s, one that did not about 3 s. So proofs that can share one invocation do: a
module-scoped fixture runs the child once and several small tests assert its
reports. A proof keeps its own session when its selection, exit code, or first
collection error is the thing under test, because the collection hook stops at
the first failing suite. The cost of sharing: a regression that crashes a shared
child fails every test that reads it, not only the one that names the property.

Why the suites are several files: CI assigns whole test files to shards, so one
file holding every session is one indivisible unit. Each file groups the sessions
of one behaviour, and a module-scoped session stays in the file whose tests read it.
"""

import ast
import json
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").is_file())

# Children load only the plugins these specimens use. Autoloading every
# installed plugin (ddtrace, typeguard, benchmark, ...) is start-up paid by each
# child and each of its xdist workers, which inherit this environment. Coverage
# of a subprocess starts from coverage's own .pth hook, never a pytest plugin,
# and CI's pytest-cov run configures none, so children stay untraced either way.
# A specimen that needs another plugin's fixture (mocker, time_machine, ...) must
# add that plugin here. A missing one fails the child with "fixture ... not
# found", never as a silent skip.
CHILD_PLUGINS = "-p pytest_django.plugin -p xdist.plugin -p pytest_asyncio.plugin -p pytest_obligation.pytest_plugin"

COLLECTION_PLUGIN = "due.pytest_plugin"
EFFECT_PLUGINS = f"pytest_plugins = ['{COLLECTION_PLUGIN}', 'due.django.conformance.reporting']"

EFFECT_DEFINITIONS = """
    from django.conf import settings
    from django.test.utils import isolate_apps
    from due.django.runtime.registry import installed
    from tests.harness.primitive import PrimitiveWork, primitive
    from due.django.runtime.registry import DurableWorkRegistration, recovery_schedules
    from dataclasses import replace

    with isolate_apps("tests"):
        class OtherWork(PrimitiveWork):
            class Meta:
                app_label = "tests"
                proxy = True

    room_storage_cleanup = primitive
    profile_rendition_cleanup = replace(primitive, model=OtherWork)
    entries = [
        DurableWorkRegistration("primitive_self_test", room_storage_cleanup, 60, "celery"),
        DurableWorkRegistration("other_self_test", profile_rendition_cleanup, 60, "celery"),
    ]
    installed.get().clear()
    installed.get().update({entry.name: entry for entry in entries})
    settings.CELERY_BEAT_SCHEDULE.update(recovery_schedules(entries))
"""

EFFECTS = """
    from effects import room_storage_cleanup, profile_rendition_cleanup
    from tests.harness.primitive import primitive
    from dataclasses import replace
    from due.django.conformance.suite import EffectExamples, effect_contract_suite

    def unused():
        # ARRANGE: no product fixture is constructed in collection tests.
        # REAL PRODUCTION: only the registered metadata check may execute.
        # EXTERNAL SEAM: no provider is called or substituted here.
        # OBSERVE: pytest exit codes and its emitted report, below.
        raise AssertionError('collection examples must not execute')

    examples = EffectExamples(
        make_work=unused, delivery=unused, retry=unused, terminal=unused, replay=unused,
        immutable_inputs='An immutable captured cleanup identity.',
        perturb_inputs=unused, producers={'declared': unused},
    )

    @effect_contract_suite(room_storage_cleanup, examples=examples)
    class TestRoom:
        pass

    @effect_contract_suite(profile_rendition_cleanup, examples=examples)
    class TestPicture:
        pass
"""

OBSERVER_DEFINITION = """
from django.conf import settings
from due.django.runtime.registry import installed, DurableWorkRegistration, recovery_schedules
from tests.harness.observation import ObservedPublication, ObservedWork
from tests.harness.primitive import PrimitiveOwner
from due.django.public import DurableWorkPolicy

observed = ObservedPublication.for_owner(
    owner=PrimitiveOwner, work=ObservedWork, input_fields=("universe", "payload"),
    policy=DurableWorkPolicy(retention=None),
)
entry = DurableWorkRegistration("observer", observed)
installed.get()[entry.name] = entry
settings.CELERY_BEAT_SCHEDULE.update(recovery_schedules([entry]))

"""

OBSERVER_SUITE = """
from observer_effect import observed

@effect_contract_suite(observed, examples=EffectExamples(
    make_work=unused, delivery=unused, retry=unused, terminal=unused,
    applied_receipt=unused, receipt_repair=unused, rejections={"rejected": unused},
    uncertainties={"unknown": unused}, observation=unused,
    immutable_inputs="One immutable provider occurrence.",
))
class TestObserver:
    pass
"""


@dataclass(frozen=True)
class ChildRun:
    path: Path
    result: pytest.RunResult

    def report(self, name: str) -> dict[str, Any]:
        return json.loads((self.path / name).read_text())

    def assert_outcomes(self, **outcomes: int) -> None:
        """The executed session finished normally, with exactly these outcome counts."""
        # A crash inside a report hook still prints a summary line; reject it explicitly.
        assert "INTERNALERROR" not in self.result.stdout.str(), self.result.stdout.str()
        self.result.assert_outcomes(**outcomes)


class ChildSession:
    """
    A throwaway pytest project outside the checkout, run by a fresh interpreter.

    It replaces function-scoped ``pytester`` so that one child can serve a module-scoped fixture.
    Unlike pytester it does not isolate ``HOME`` or force ``PY_COLORS=0``. The assertions
    tolerate that: they read report files and summary text, the child writes to a pipe so
    pytest adds no colour unless ``PY_COLORS``/``FORCE_COLOR`` is exported (CI exports
    neither), and the child's own ``pytest.ini`` is its only pytest configuration.
    """

    def __init__(self, path: Path, conftest: str = EFFECT_PLUGINS) -> None:
        self.path = path
        self.write("pytest.ini", "[pytest]\nDJANGO_SETTINGS_MODULE = tests.settings\n")
        self.write(
            "conftest.py", conftest + "\nfrom examples.celery_app import app\napp.set_default()\napp.set_current()\n"
        )
        self.write("effects.py", EFFECT_DEFINITIONS)
        self.write("observer_effect.py", OBSERVER_DEFINITION)
        self.write("test_effects.py", EFFECTS)

    def write(self, name: str, source: str) -> None:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(source).strip() + "\n")

    def edit(self, name: str, old: str, new: str) -> None:
        source = (self.path / name).read_text()
        assert source.count(old) == 1, f"{name} must contain {old!r} exactly once"
        self.write(name, source.replace(old, new))

    def append(self, name: str, source: str) -> None:
        with (self.path / name).open("a") as target:
            target.write(source)

    def run(self, *args: str) -> ChildRun:
        env = {
            **os.environ,
            # Child pytest runs outside the checkout; CI does not export its import path.
            "PYTHONPATH": os.pathsep.join(filter(None, [str(self.path), str(REPO_ROOT), os.environ.get("PYTHONPATH")])),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_ADDOPTS": CHILD_PLUGINS,
        }
        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", f"--basetemp={self.path / '.basetemp'}", *args],
            cwd=self.path,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        result = pytest.RunResult(
            completed.returncode,
            completed.stdout.splitlines(),
            completed.stderr.splitlines(),
            time.monotonic() - started,
        )
        return ChildRun(self.path, result)


def render_observation_report(report: Path, *options: str) -> str:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest_obligation.evidence.observation_report",
            str(report),
            *options,
        ],
        text=True,
        capture_output=True,
        check=True,
    ).stdout


def add_room_eligibility(child: ChildSession) -> None:
    child.edit(
        "effects.py",
        "room_storage_cleanup = primitive",
        "from due.django.public import ExecutionEligibility\n"
        "from django.db.models import Q\n"
        "room_storage_cleanup = replace(primitive, eligibility=ExecutionEligibility(lambda now: Q(owner__value='ready')))",
    )
    child.edit(
        "test_effects.py",
        "@effect_contract_suite(room_storage_cleanup, examples=examples)",
        "@effect_contract_suite(room_storage_cleanup, examples=replace(examples, "
        "eligibility={'active_owner': unused, 'unsettled_dependency': unused}))",
    )


HANDOFF_CASE = "test_due_work_contract[handoff-accepted-work-assert_crash_at_every_commit_converges]"


def add_room_handoff(child: ChildSession) -> None:
    child.edit(
        "test_effects.py",
        "examples = EffectExamples(",
        "from pytest_obligation.crash_histories import HandoffHistory\n"
        "history = HandoffHistory(name='accepted-work', arrange=unused, transition=primitive.attempt_execution, observe=unused)\n"
        "examples = EffectExamples(handoffs=(history,),",
    )


def lost_proofs(result: pytest.RunResult, suite: str) -> list[str]:
    """The one collection failure the hook raised, parsed back into the missing case names."""
    marker = f"{suite}: registered capability lost collected proofs: "
    lines = [line for line in result.outlines if marker in line]
    assert lines, result.stdout.str()
    return ast.literal_eval(lines[0][lines[0].index(marker) + len(marker) :])


@pytest.fixture(name="child")
def child_fixture(tmp_path: Path) -> ChildSession:
    return ChildSession(tmp_path)
