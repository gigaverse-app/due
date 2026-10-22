"""Keep Due's workflow routing tied to real repository inputs and Git changes."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from pytest_selective.ci import LaneConfig, plan

ROOT = Path(__file__).resolve().parents[1]


class RoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.repo = Path(cls.temporary.name)
        archive = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, check=True, capture_output=True).stdout
        subprocess.run(["tar", "-xf", "-", "-C", str(cls.repo)], input=archive, check=True)
        cls.config = LaneConfig.model_validate_json((ROOT / ".github/test-lanes.json").read_text())
        cls.git("init", "-q")
        cls.git("config", "user.email", "ci@example.invalid")
        cls.git("config", "user.name", "CI routing test")
        cls.base = cls.commit()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def git(cls, *args):
        return subprocess.check_output(["git", *args], cwd=cls.repo, text=True).strip()

    @classmethod
    def commit(cls):
        cls.git("add", "-A")
        cls.git("commit", "-qm", "routing scenario")
        return cls.git("rev-parse", "HEAD")

    def selection(self, path, *, delete=False):
        self.git("reset", "--hard", self.base)
        file = self.repo / path
        if delete:
            file.unlink()
        else:
            file.parent.mkdir(parents=True, exist_ok=True)
            with file.open("a") as output:
                output.write("\n# Routing scenario\n")
        result = plan(self.repo, self.config, self.base, self.commit())
        self.assertNotIn("fallback", result.reason, result.model_dump_json())
        return {name for name, enabled in result.lanes.items() if enabled}

    def test_prose_and_repository_metadata_do_not_start_runtime_lanes(self):
        for path in (
            "README.md",
            "package/README.md",
            "package/docs/conformance.md",
            ".github/dependabot.yml",
            ".github/pull_request_template.md",
            ".github/workflows/observe-ci.yml",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.selection(path), set())

    def test_workflow_changes_only_run_their_execution_lanes(self):
        self.assertEqual(self.selection(".github/workflows/ci.yml"), {"library", "integration", "typecheck"})
        self.assertEqual(self.selection(".github/workflows/widget-store.yml"), {"widget_comparison"})

    def test_environment_and_shared_runtime_keep_full_validation(self):
        for path in ("uv.lock", ".github/test-lanes.json", "package/src/due/__init__.py"):
            with self.subTest(path=path):
                self.assertEqual(self.selection(path), set(self.config.lanes))

    def test_widget_subprocess_helper_and_bootstrap_keep_benchmark_coverage(self):
        for path in (
            "integration_tests/integration/processes.py",
            "examples/settings.py",
            "examples/celery_app.py",
            "scripts/widget_profile.py",
        ):
            with self.subTest(path=path):
                self.assertIn("widget_comparison", self.selection(path))

    def test_unrelated_application_and_validator_skip_widget_benchmarks(self):
        for path in ("examples/books/models.py", "scripts/check_adoption_report.py"):
            with self.subTest(path=path):
                selected = self.selection(path)
                self.assertIn("integration", selected)
                self.assertNotIn("widget_comparison", selected)

    def test_deleted_library_test_still_runs_completeness_controls(self):
        path = next((self.repo / "package/tests").rglob("test_*.py")).relative_to(self.repo).as_posix()
        self.assertIn("library", self.selection(path, delete=True))

    def test_missing_history_and_full_checkpoint_run_every_lane(self):
        for result in (
            plan(self.repo, self.config, "missing-history", self.base),
            plan(self.repo, self.config, self.base, self.base, full=True),
        ):
            self.assertTrue(all(result.lanes.values()), result.model_dump_json())


if __name__ == "__main__":
    unittest.main()
