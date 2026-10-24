"""Exercise the deployed matrix adapter: only literal successful proof skips work."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
from ci_esta.resources import source_root

ROOT = Path(__file__).resolve().parents[1]


class EvidenceMatrixTests(unittest.TestCase):
    def scope_outputs(self, lanes, outcome, plan):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        step = next(step for step in workflow["jobs"]["select-tests"]["steps"] if step.get("id") == "evidence")
        self.assertIn("ci-esta/actions/resolve-lane-evidence@", step["uses"])
        self.assertEqual(step["with"]["selection"], "${{ steps.policy.outputs.admitted }}")
        self.assertEqual(step["with"]["selection-outcome"], "${{ steps.select.outcome }}")
        action = source_root() / "actions/resolve-lane-evidence"
        deployed = yaml.safe_load((action / "action.yml").read_text())["runs"]["steps"][0]["with"]["script"]
        script = """
const outputs = {};
const core = {setOutput: (key, value) => outputs[key] = value};
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('core','require','process',process.env.ACTION_SCRIPT)(core,require,process)
.then(() => console.log(JSON.stringify(outputs))).catch(error => { console.error(error); process.exit(1); });
"""
        with tempfile.TemporaryDirectory() as directory:
            if plan is not None:
                (Path(directory) / step["with"]["selection-report"]).write_text(
                    plan if isinstance(plan, str) else json.dumps(plan), encoding="utf-8"
                )
            result = subprocess.run(
                ["node", "-e", script],
                cwd=directory,
                env={
                    **os.environ,
                    "SELECTION": lanes,
                    "SELECTION_OUTCOME": outcome,
                    "SELECTION_KEYS": step["with"]["selection-keys"],
                    "SELECTION_REPORT": str(Path(directory) / step["with"]["selection-report"]),
                    "EVIDENCE_LANES": step["with"]["lanes"],
                    "GITHUB_ACTION_PATH": str(action),
                    "ALLOW_REUSE": "false",
                    "ACTION_SCRIPT": deployed,
                },
                capture_output=True,
                text=True,
                check=True,
            )
        outputs = json.loads(result.stdout)
        self.assertEqual(outputs["receipt"], "")
        return {**json.loads(outputs["selection"]), **outputs}

    def test_fallback_or_unverifiable_plans_cannot_reuse_or_issue_receipts(self):
        lanes = {"library": True, "integration": True}
        normal = {"lanes": lanes, "reason": "global CI/dependency input"}
        for plan, reusable in (
            (normal, True),
            ({**normal, "reason": "full-suite fallback: missing history"}, False),
            ({**normal, "reason": "explicit full-suite checkpoint"}, False),
            ({**normal, "reason": ""}, False),
            ({"lanes": lanes}, False),
            ({**normal, "lanes": {"library": False, "integration": True}}, False),
            (None, False),
            ("broken JSON", False),
        ):
            with self.subTest(plan=plan):
                outputs = self.scope_outputs(json.dumps(lanes), "success", plan)
                self.assertEqual(outputs["reusable"], reusable)
        for raw in ("{}", "null", '{"library":"true","integration":true}'):
            with self.subTest(raw=raw):
                self.assertFalse(self.scope_outputs(raw, "success", normal)["reusable"])

    def test_execution_scope_distinguishes_partial_validation_from_full_fallback(self):
        for lanes, outcome, library, integration in (
            ('{"library":true,"integration":true}', "success", True, True),
            ('{"library":true,"integration":false}', "success", True, False),
            ('{"library":false,"integration":true}', "success", False, True),
            ('{"library":false,"integration":false}', "success", False, False),
            ('{"library":"false","integration":null}', "success", True, True),
            ('{"library":false,"integration":false}', "failure", True, True),
            ("{}", "success", True, True),
            ("null", "success", True, True),
            ("invalid", "success", True, True),
        ):
            with self.subTest(lanes=lanes, outcome=outcome):
                outputs = self.scope_outputs(lanes, outcome, None)
                self.assertEqual(
                    {key: outputs[key] for key in ("library", "integration")},
                    {"library": library, "integration": integration},
                )
                self.assertEqual(json.loads(outputs["context"]), {"library": library, "integration": integration})

    def test_only_proven_django_variants_leave_the_execution_matrix(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        step = next(step for step in workflow["jobs"]["select-tests"]["steps"] if step.get("id") == "evidence")
        self.assertEqual(workflow["jobs"]["select-tests"]["outputs"]["django"], "${{ steps.evidence.outputs.matrix }}")
        keys = list(json.loads(step["with"]["lanes"]))
        script = """
const {pendingMatrix} = require(process.env.SHARED_SELECTION);
console.log(JSON.stringify(pendingMatrix(JSON.parse(process.env.MATRIX_KEYS), process.env.REUSED)));
"""
        for proof, expected in (
            ({}, ["locked", "minimum"]),
            ({"locked": True}, ["minimum"]),
            ({"minimum": True}, ["locked"]),
            ({"locked": False, "minimum": False}, ["locked", "minimum"]),
            ({"locked": "true", "minimum": 1}, ["locked", "minimum"]),
            ({"locked": None, "minimum": None}, ["locked", "minimum"]),
            # Both proven: job admission skips execution. Keep a valid fallback
            # matrix so GitHub never has to expand an empty matrix.
            ({"locked": True, "minimum": True}, ["locked", "minimum"]),
        ):
            with self.subTest(proof=proof):
                result = subprocess.run(
                    ["node", "-e", script],
                    env={
                        **os.environ,
                        "REUSED": json.dumps(proof),
                        "MATRIX_KEYS": json.dumps(keys),
                        "SHARED_SELECTION": str(source_root() / "actions/resolve-lane-evidence/selection.cjs"),
                    },
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(json.loads(result.stdout), expected)
