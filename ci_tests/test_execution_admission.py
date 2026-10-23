"""Every runtime test command must be admitted by dependency selection."""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (
    "pytest ",
    "-m unittest",
    "node --test",
    "run_ci_tests.py",
    "smoke_distribution.py",
    "check_wheel.py",
    "check_core_is_framework_free.py",
    "scripts/tutorial.py",
    "ci-esta-pytest",
)


class ExecutionAdmissionTests(unittest.TestCase):
    def test_runtime_commands_have_selection_and_failure_fallback(self):
        for path in (ROOT / ".github/workflows").glob("*.y*ml"):
            # Release/full checkpoints deliberately execute everything.
            if path.name not in {"ci.yml", "public-actions.yml"}:
                continue
            jobs = yaml.safe_load(path.read_text())["jobs"]
            for name, job in jobs.items():
                for step in job.get("steps", []):
                    command = step.get("run", "")
                    if not any(marker in command for marker in RUNTIME):
                        continue
                    admission = job.get("if", "") + " " + step.get("if", "")
                    assert "needs." in admission and "result != 'success'" in admission, (path.name, name, command)
                    assert any(output in admission for output in ("lanes", "outputs.library", "outputs.integration")), (
                        path.name,
                        name,
                        command,
                    )

    def test_public_smoke_is_selected_and_requires_successful_planning(self):
        path = ROOT / ".github/workflows/public-actions.yml"
        if not path.exists():
            return
        jobs = yaml.safe_load(path.read_text())["jobs"]
        smoke = jobs["public-actions"]
        assert smoke["needs"] == "select-tests"
        assert "needs.select-tests.result != 'success'" in smoke["if"]
        assert "lanes" in smoke["if"]
        assert any(
            "exit 1" in step.get("run", "") and "needs.select-tests.result" in step.get("if", "")
            for step in smoke["steps"]
        )
