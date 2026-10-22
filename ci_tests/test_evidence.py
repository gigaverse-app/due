"""Exercise the deployed matrix adapter: only literal successful proof skips work."""

import json
import os
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class EvidenceMatrixTests(unittest.TestCase):
    def test_only_proven_django_variants_leave_the_execution_matrix(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        step = next(step for step in workflow["jobs"]["select-tests"]["steps"] if step.get("id") == "matrix")
        script = "const core = {setOutput: (key, value) => console.log(value)};\n" + step["with"]["script"]
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
                    env={**os.environ, "REUSED": json.dumps(proof)},
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(json.loads(result.stdout), expected)
