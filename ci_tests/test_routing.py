"""Keep Due's workflow routing tied to real repository inputs and Git changes."""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
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

    def selection(self, path, *, delete=False, edit=None):
        self.git("reset", "--hard", self.base)
        file = self.repo / path
        if delete:
            file.unlink()
        elif edit is not None:
            file.write_text(edit(file.read_text()))
        else:
            file.parent.mkdir(parents=True, exist_ok=True)
            with file.open("a") as output:
                output.write("\n# Routing scenario\n")
        result = plan(self.repo, self.config, self.base, self.commit())
        self.assertNotIn("fallback", result.reason, result.model_dump_json())
        return {name for name, enabled in result.lanes.items() if enabled}

    @staticmethod
    def with_scenario_env(*keys):
        """Change one mapping of a workflow; the planner compares parsed YAML, not layout."""

        def edit(text):
            document = yaml.safe_load(text)
            target = document
            for key in keys:
                target = target[key]
            target.setdefault("env", {})["ROUTING_SCENARIO"] = "1"
            return yaml.safe_dump(document, sort_keys=False)

        return edit

    def test_prose_and_repository_metadata_do_not_start_runtime_lanes(self):
        for path in (
            "README.md",
            "package/README.md",
            "package/docs/conformance.md",
            "docs/guide.rst",
            "md/guide.md",
            ".github/ISSUE_TEMPLATE/bug.md",
            ".github/dependabot.yml",
            ".github/pull_request_template.md",
            ".github/workflows/observe-ci.yml",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.selection(path), set())

    def test_each_workflow_job_runs_only_the_lanes_it_executes(self):
        # Every job is listed, so a new job must be classified here before it merges.
        executes = {
            ".github/workflows/ci.yml": {
                "ci-contracts": {"contracts"},
                "select-tests": {"contracts"},
                "lint": set(),
                "tests": {"library", "integration"},
                "package-export": {"library"},
                "typecheck": {"typecheck"},
                "test-gates": {"contracts"},
                "build": {"wheel"},
                "validation-gate": {"contracts"},
            },
            ".github/workflows/widget-store.yml": {
                "select-tests": {"contracts"},
                "compare": {"widget_comparison"},
                "comparison-gate": {"contracts"},
            },
        }
        for path, lanes_by_job in executes.items():
            jobs = yaml.safe_load((ROOT / path).read_text())["jobs"]
            self.assertEqual(set(jobs), set(lanes_by_job), "classify each job's execution dependencies")
            for job in jobs:
                with self.subTest(path=path, job=job):
                    selected = self.selection(path, edit=self.with_scenario_env("jobs", job))
                    self.assertEqual(selected, lanes_by_job[job] | {"contracts"})

    def test_settings_every_job_inherits_run_the_workflow_lanes(self):
        for path, lanes in (
            (".github/workflows/ci.yml", {"library", "integration", "typecheck", "contracts", "wheel"}),
            (".github/workflows/widget-store.yml", {"widget_comparison", "contracts"}),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.selection(path, edit=self.with_scenario_env()), lanes)

    def test_workflow_edits_always_validate_the_complete_execution_contract(self):
        def bump(text):
            return re.sub(r"(plan-lanes@)v[0-9]+(?:\.[0-9]+)*", r"\g<1>v99", text)

        for path in (".github/workflows/ci.yml", ".github/workflows/widget-store.yml"):
            with self.subTest(path=path, change="pin"):
                self.assertNotEqual(bump((ROOT / path).read_text()), (ROOT / path).read_text())
                self.assertEqual(self.selection(path, edit=bump), {"contracts"})
            with self.subTest(path=path, change="comment"):
                self.assertEqual(self.selection(path), {"contracts"})

    def test_nested_or_new_pytest_collection_policy_forces_every_lane(self):
        for path in ("package/pyproject.toml", "package/.pytest.ini"):
            with self.subTest(path=path):
                self.git("reset", "--hard", self.base)
                self.git("clean", "-fd")
                target = self.repo / path
                if target.exists():
                    target.write_text(
                        target.read_text().replace('testpaths = ["tests"]', 'testpaths = ["other_tests"]')
                    )
                else:
                    target.write_text("[pytest]\ntestpaths = other_tests\n")
                result = plan(self.repo, self.config, self.base, self.commit())
                self.assertTrue(all(result.lanes.values()), result.model_dump_json())
                self.assertIn("fallback", result.reason)

    def test_environment_and_shared_runtime_keep_full_validation(self):
        for path in (
            "uv.lock",
            ".github/test-lanes.json",
            "package/src/due/__init__.py",
            "requirements.txt",
            "package/requirements-dev.txt",
            "poetry.lock",
            "pylock.toml",
        ):
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
        for path in (
            "examples/books/models.py",
            "scripts/check_adoption_report.py",
            "examples/acme_printworks/acme/effects.py",
            "examples/acme_printworks/tests/test_contracts.py",
            "scripts/check_acme_printworks.py",
        ):
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

    def test_documentation_dependency_selects_its_declared_reader(self):
        self.git("reset", "--hard", self.base)
        reader = self.repo / "package/tests/test_declared_doc.py"
        reader.parent.mkdir(parents=True, exist_ok=True)
        reader.write_text("# gv:test-dependency: md/declared.md\ndef test_document(): pass\n")
        doc = self.repo / "md/declared.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("before\n")
        base = self.commit()
        doc.write_text("after\n")
        result = plan(self.repo, self.config, base, self.commit())
        self.assertTrue(result.lanes["library"], result.model_dump_json())
        self.assertNotIn("fallback", result.reason)

    def test_execution_helpers_select_contracts_and_wheel(self):
        self.assertIn("contracts", self.selection("ci_tests/test_routing.py"))
        self.assertIn("wheel", self.selection("scripts/check_wheel.py"))


if __name__ == "__main__":
    unittest.main()
