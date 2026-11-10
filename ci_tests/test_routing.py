"""Keep Due's workflow routing tied to real repository inputs and Git changes."""

import contextlib
import io
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
from ci_esta.testing.lane_inference import resolve
from ci_esta.testing.work_plan import main as plan_work_main
from ci_esta.testing.work_plan import plan_work

ROOT = Path(__file__).resolve().parents[1]


class RoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.repo = Path(cls.temporary.name)
        archive = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, check=True, capture_output=True).stdout
        subprocess.run(["tar", "-xf", "-", "-C", str(cls.repo)], input=archive, check=True)
        cls.git("init", "-q")
        cls.git("config", "user.email", "ci@example.invalid")
        cls.git("config", "user.name", "CI routing test")
        cls.base = cls.commit()
        cls.lanes = set(resolve(cls.repo, cls.base, cls.base, cls.repo / ".github/test-lanes.json").config.lanes)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def git(cls, *args):
        return subprocess.check_output(["git", *args], cwd=cls.repo, text=True).strip()

    @classmethod
    def plan(cls, base, head, *, full=False):
        """CI-esta plan-work as the select-tests job runs it: lanes inferred from the workflows, then the lane file."""
        resolved = resolve(cls.repo, base, head, cls.repo / ".github/test-lanes.json")
        return plan_work(cls.repo, resolved.config, base, head, full=full, rules=resolved.rules)

    @classmethod
    def commit(cls):
        cls.git("add", "-A")
        cls.git("commit", "-qm", "routing scenario")
        return cls.git("rev-parse", "HEAD")

    def selection(self, path, *, delete=False, edit=None):
        return self.commit_selection((path,), delete=delete, edit=edit)

    def commit_selection(self, paths, *, delete=False, edit=None):
        """Lanes selected by one real commit that changes every path."""
        self.git("reset", "--hard", self.base)
        self.git("clean", "-fd")
        for path in paths:
            file = self.repo / path
            if delete:
                file.unlink()
            elif edit is not None:
                file.write_text(edit(file.read_text()))
            else:
                file.parent.mkdir(parents=True, exist_ok=True)
                with file.open("a") as output:
                    output.write("\n# Routing scenario\n")
        result = self.plan(self.base, self.commit())
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
            "package/docs/conformance.md",
            "docs/guide.rst",
            "md/guide.md",
            "mkdocs.yml",
            ".readthedocs.yaml",
            # Read the Docs dependencies are not the test environment.
            "documentation/requirements.in",
            "documentation/requirements.txt",
            "examples/README.md",
            "examples/books/README.md",
            "examples/widget_store/README.md",
            "examples/acme_printworks/README.md",
            # Standalone install instructions; CI runs Acme against the locked checkout environment.
            "examples/acme_printworks/requirements.txt",
            ".github/ISSUE_TEMPLATE/bug.md",
            ".github/dependabot.yml",
            ".github/pull_request_template.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.selection(path), set())
        # The CI contract tests plan every workflow, so any workflow edit runs them, and only them.
        with self.subTest(path=".github/workflows/observe-ci.yml"):
            self.assertEqual(self.selection(".github/workflows/observe-ci.yml"), {"contracts"})

    def test_documentation_only_commit_runs_no_lane(self):
        self.assertEqual(
            self.commit_selection(
                (
                    "README.md",
                    "docs/architecture.md",
                    "package/docs/quickstart.md",
                    "examples/acme_printworks/README.md",
                    "documentation/index.md",
                    "documentation/requirements.in",
                    "documentation/requirements.txt",
                    "mkdocs.yml",
                )
            ),
            set(),
        )

    def test_shared_declarations_select_their_suite_and_routing_checks(self):
        # A declaration edit validates its readers and this planner check, without
        # starting the other suite, widget benchmark, wheel or Python type checking.
        for path, suite in (
            ("package/tests/.gv-dependencies.yaml", "library"),
            ("integration_tests/.gv-dependencies.yaml", "integration"),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.selection(path), {suite, "contracts"})

    def test_distribution_contents_select_only_the_package_build(self):
        # The package README is the published long description; check_metadata.py compares it.
        for path in (
            "package/README.md",
            "package/LICENSE",
            "scripts/ci/check_metadata.py",
            "scripts/check_published_testing_wheel.py",
            "scripts/check_testing_wheel.py",
        ):
            with self.subTest(path=path):
                self.assertIn("wheel", self.selection(path))
        with self.subTest(path="package/LICENSE"):
            self.assertEqual(self.selection("package/LICENSE"), {"wheel"})
        # ci_tests/test_metadata.py imports check_metadata.py, which names the README: the contract tests run too.
        with self.subTest(path="package/README.md"):
            self.assertEqual(self.selection("package/README.md"), {"wheel", "contracts"})

    def test_example_code_and_templates_select_their_lanes(self):
        # Narrowed inputs must still match files directly inside each directory and nested below it.
        for path, lanes in (
            ("examples/urls.py", {"integration"}),
            ("examples/books/views.py", {"integration"}),
            ("examples/widget_store/views.py", {"integration", "widget_comparison"}),
            ("examples/widget_store/migrations/0001_initial.py", {"integration", "widget_comparison"}),
        ):
            with self.subTest(path=path):
                self.assertLessEqual(lanes, self.selection(path))
        # Templates are rendered by the live journeys and the widget benchmark.
        for path, lanes in (
            ("examples/books/templates/books/form.html", {"integration"}),
            ("examples/scheduled/templates/scheduled/list.html", {"integration"}),
            ("examples/widget_store/templates/widget_store/store.html", {"integration", "widget_comparison"}),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.selection(path), lanes)

    def test_each_workflow_job_runs_only_the_lanes_it_executes(self):
        # Every job is listed, so a new job must be classified here before it merges.
        executes = {
            ".github/workflows/ci.yml": {
                "ci-contracts": {"contracts"},
                "python-contracts": {"source_contracts"},
                # The planner: an edit to it, its pin included, re-plans every lane.
                "select-tests": set(self.lanes),
                "lint": set(),
                "tests": {"library", "integration"},
                "package-export": {"library"},
                "typecheck": {"typecheck"},
                "test-gates": {"library", "integration"},
                "build": {"wheel"},
                "validation-gate": {"contracts"},
            },
            ".github/workflows/widget-store.yml": {
                "select-tests": set(self.lanes),
                "compare": {"widget_comparison"},
                "comparison-gate": {"widget_comparison"},
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
        # Workflow-level settings reach the planner job, which plans every lane.
        for path in (".github/workflows/ci.yml", ".github/workflows/widget-store.yml"):
            with self.subTest(path=path):
                self.assertEqual(self.selection(path, edit=self.with_scenario_env()), self.lanes)

    def test_workflow_edits_always_validate_the_complete_execution_contract(self):
        def bump(text):
            return re.sub(r"(plan-work@)v[0-9]+(?:\.[0-9]+)*", r"\g<1>v99", text)

        for path in (".github/workflows/ci.yml", ".github/workflows/widget-store.yml"):
            # A new planner revision may select differently: it re-plans every lane, the contracts included.
            with self.subTest(path=path, change="pin"):
                self.assertNotEqual(bump((ROOT / path).read_text()), (ROOT / path).read_text())
                self.assertEqual(self.selection(path, edit=bump), self.lanes)
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
                result = self.plan(self.base, self.commit())
                self.assertTrue(all(result.lanes.values()), result.model_dump_json())
                self.assertIn("fallback", result.reason)

    def test_environment_and_shared_runtime_keep_full_validation(self):
        # The test environment is declared explicitly; Due resolves it with uv only.
        for path in (
            "uv.lock",
            "pyproject.toml",
            "package/pyproject.toml",
            ".python-version",
            ".github/test-lanes.json",
            "package/src/due/__init__.py",
        ):
            with self.subTest(path=path):
                # The planner reads the lane file, so the scenario keeps it valid JSON.
                edit = (lambda text: text + "\n") if path.endswith(".json") else None
                self.assertEqual(self.selection(path, edit=edit), self.lanes)

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
        # Not the bundled example apps: the import graph follows the module names Django settings hold
        # (examples.celery_app -> examples.settings -> examples.urls -> every app), so they reach the storefront.
        # The test policy still defers the benchmark on ordinary pull requests.
        for path in (
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
        # A first push has no base (all zeros): plan-work compares against nothing and runs every lane.
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = plan_work_main(
                [
                    "--repo",
                    str(self.repo),
                    "--config",
                    str(self.repo / ".github/test-lanes.json"),
                    "--base",
                    "0" * 40,
                    "--head",
                    self.base,
                ]
            )
        self.assertEqual(code, 0)
        for lanes in (json.loads(output.getvalue())["lanes"], self.plan(self.base, self.base, full=True).lanes):
            self.assertTrue(all(lanes.values()), lanes)

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
        result = self.plan(base, self.commit())
        self.assertTrue(result.lanes["library"], result.model_dump_json())
        self.assertNotIn("fallback", result.reason)

    def test_quint_contract_inputs_select_contracts(self):
        for path in (
            "docs/quint/report-provenance/fixed.qnt",
            "docs/quint/report-provenance/audit.qnt",
            "docs/quint/report-provenance/README.md",
            "scripts/ci/check_quint_report.py",
            "scripts/ci/replay_report_provenance.py",
            "scripts/ci/check_report_source.py",
            "ci_tests/test_report_source.py",
            ".github/workflows/obligation-report.yml",
            "package.json",
            "package-lock.json",
        ):
            with self.subTest(path=path):
                self.assertIn("contracts", self.selection(path))

    def test_execution_helpers_select_contracts_and_wheel(self):
        self.assertIn("contracts", self.selection("ci_tests/test_routing.py"))
        self.assertIn("wheel", self.selection("scripts/check_wheel.py"))

    def test_source_proof_and_native_bridge_dependencies_select_the_contract_lane(self):
        for path in (
            "package/src/due/retry_delays.py",
            "package/src/due/retry_delays.proofs.dfy",
            "package/src/due/claim_authority.py",
            "package/src/due/django/runtime/coordination.py",
            "package/src/due/django/runtime/settlement_batch.py",
            "package/tests/harness/self_work.py",
            "package/tests/harness/claim_settlement.py",
            "package/tests/source_contracts/test_settlement.py",
            "package/tests/conformance/test_exploration_collection.py",
            "proofs/generated/claim-authority.dfy",
            "veripy-project.json",
            "contracts/python-checks.json",
            "tooling/source-contracts/verifier.patch",
            "tooling/source-contracts/requirements-project.lock",
            "tooling/source-contracts/requirements-build.lock",
            "scripts/check_python_contracts.py",
            "scripts/bootstrap_source_verifier.py",
        ):
            with self.subTest(path=path):
                self.assertIn("source_contracts", self.selection(path))


if __name__ == "__main__":
    unittest.main()
