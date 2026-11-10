"""The widget comparison is optional on ordinary PRs, even when affected."""
# Plans the workflows' admission with the repository's policy and lanes.
# gv:test-dependency: .github/workflows/ci.yml
# gv:test-dependency: .github/workflows/widget-store.yml
# gv:test-dependency: .github/test-policy.json
# gv:test-dependency: .github/test-lanes.json

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ci_esta.policy import ExecutionPlan
from ci_esta.policy.cli import main
from ci_esta.testing.lane_inference import resolve
from ci_esta.testing.test_policy import workflow_inputs

ROOT = Path(__file__).resolve().parents[1]


def plan_workflow(workflow, report, context):
    """Run the workflow's real policy inputs against the planner's report, as the select-tests job does."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".github").mkdir()
        shutil.copy(ROOT / ".github/test-policy.json", root / ".github/test-policy.json")
        inputs = workflow_inputs(workflow, "select-tests", context, root=root)
        # plan-work writes its report at the workspace root; the policy step reads its lanes from there.
        Path(inputs["lanes"]).write_text(json.dumps(report), encoding="utf-8")
        destination = root / "plan.json"
        args = ["--output", str(destination), "--github-output", ""]
        for key, value in inputs.items():
            if key != "output":
                args.extend([f"--{key}", value])
        if main(args) != 0:
            raise AssertionError("Production policy inputs failed planning")
        return ExecutionPlan.model_validate_json(destination.read_text(encoding="utf-8"))


class ProfileRoutingTests(unittest.TestCase):
    def test_real_workflows_route_profiles_without_copied_defaults(self):
        inventory = resolve(ROOT, "HEAD", "HEAD", ROOT / ".github/test-lanes.json").config.lanes
        for workflow in ("ci.yml", "widget-store.yml"):
            for event, labels, changed, expected in (
                ("pull_request", [], ["library", "widget_comparison"], {"library"}),
                ("pull_request", ["test:affected-deep"], ["widget_comparison"], {"widget_comparison"}),
                ("pull_request", ["test:affected-deep"], ["library"], {"library"}),
                ("schedule", [], [], set(inventory)),
            ):
                with self.subTest(workflow=workflow, event=event, labels=labels, changed=changed):
                    lanes = {name: name in changed for name in inventory}
                    result = plan_workflow(
                        ROOT / ".github/workflows" / workflow,
                        {"lanes": lanes, "reason": "affected tests"},
                        {
                            "github": {
                                "event_name": event,
                                "sha": "abc",
                                "event": {
                                    "pull_request": {
                                        "labels": [{"name": label} for label in labels],
                                    }
                                },
                            },
                            "inputs": {},
                            "steps": {"select": {"outputs": {"lanes": json.dumps(lanes)}}},
                        },
                    )
                    self.assertEqual({item.work.id for item in result.runnable}, expected)
