"""Run the deployed README admission step against controlled GitHub API responses."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ReportSourceTests(unittest.TestCase):
    def run_step(self, fault=None, *, recheck=False):
        workflow = yaml.safe_load((ROOT / ".github/workflows/obligation-report.yml").read_text())
        steps = workflow["jobs"]["readme"]["steps"]
        step = (
            next(step for step in steps if step.get("name") == "Reject a source rerun during artifact download")
            if recheck
            else next(step for step in steps if step.get("id") == "tests")
        )
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        run = {
            "id": 17,
            "path": ".github/workflows/ci.yml",
            "event": "schedule",
            "head_branch": "main",
            "head_sha": revision,
            "status": "completed",
            "conclusion": "failure",  # An unrelated lint failure does not invalidate contract evidence.
            "run_attempt": 1,
            "repository": {"full_name": "gigaverse-app/due"},
            "head_repository": {"full_name": "gigaverse-app/due"},
        }
        jobs = [{"name": "Execute tests (locked)", "status": "completed", "conclusion": "success"}]
        if fault == "job-missing":
            jobs = []
        elif fault == "job-duplicate":
            jobs *= 2
        elif fault == "job-running":
            jobs[0]["status"] = "in_progress"
            jobs[0]["conclusion"] = None
        elif fault == "job-failed":
            jobs[0]["conclusion"] = "failure"
        elif fault == "job-second-page":
            pass
        elif fault:
            key, value = fault
            run[key] = value
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "gh"
            fake.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "data = json.loads(os.environ['API_FIXTURE'])\n"
                "if '/jobs' in sys.argv[2]:\n"
                "    data = {'jobs': data['jobs']}\n"
                "    if '--jq' in sys.argv:\n"
                "        if sys.argv[sys.argv.index('--jq') + 1] == '.jobs | @json':\n"
                "            if os.environ.get('SECOND_PAGE'):\n"
                "                print('[]')\n"
                "                if '--paginate' not in sys.argv: sys.exit(0)\n"
                "            print(json.dumps(data['jobs']))\n"
                "            sys.exit(0)\n"
                "        print('\\n'.join(str(j['conclusion']) for j in data['jobs']))\n"
                "        sys.exit(0)\n"
                "else: data = data['run']\n"
                "print(json.dumps(data))\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            output = Path(directory) / "outputs"
            output.touch()
            result = subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PATH": directory + os.pathsep + os.environ["PATH"],
                    "GITHUB_OUTPUT": str(output),
                    "GITHUB_REPOSITORY": "gigaverse-app/due",
                    "RUN_ID": "17",
                    "SOURCE_ATTEMPT": "1",
                    "SECOND_PAGE": "yes" if fault == "job-second-page" else "",
                    "API_FIXTURE": json.dumps({"run": run, "jobs": jobs}),
                },
                capture_output=True,
                text=True,
            )
            return result, output.read_text()

    def test_ineligible_source_never_reaches_rendering(self):
        for fault in (
            ("event", "pull_request"),
            ("event", "workflow_dispatch"),
            ("path", ".github/workflows/other.yml"),
            ("head_branch", "feature"),
            ("head_sha", "0" * 40),
            ("repository", {"full_name": "other/due"}),
            ("head_repository", {"full_name": "other/due"}),
            ("status", "in_progress"),
            ("run_attempt", None),
            ("id", 18),
            "job-missing",
            "job-duplicate",
            "job-running",
        ):
            with self.subTest(fault=fault):
                result, output = self.run_step(fault)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("outcome=", output)

    def test_completed_full_source_retains_the_actual_contract_outcome(self):
        for fault, outcome in ((None, "success"), ("job-failed", "failure")):
            with self.subTest(outcome=outcome):
                result, output = self.run_step(fault)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"outcome={outcome}", output)

    def test_contract_job_can_be_on_a_later_api_page(self):
        result, output = self.run_step("job-second-page")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("outcome=success", output)

    def test_rerun_during_download_is_rejected(self):
        result, _ = self.run_step(("run_attempt", 2), recheck=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed while downloading evidence", result.stderr)

    def test_admission_brackets_downloads_before_rendering(self):
        steps = yaml.safe_load((ROOT / ".github/workflows/obligation-report.yml").read_text())["jobs"]["readme"][
            "steps"
        ]
        admission = next(index for index, step in enumerate(steps) if step.get("id") == "tests")
        recheck = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Reject a source rerun during artifact download"
        )
        downloads = [
            index for index, step in enumerate(steps) if step.get("uses", "").startswith("actions/download-artifact@")
        ]
        renderer = next(
            index
            for index, step in enumerate(steps)
            if step.get("uses", "").startswith("gigaverse-app/pytest-obligation/report@")
        )
        self.assertLess(admission, min(downloads))
        self.assertLess(max(downloads), recheck)
        self.assertLess(recheck, renderer)
