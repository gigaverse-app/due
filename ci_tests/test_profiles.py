"""The widget comparison is optional on ordinary PRs, even when affected."""

import json
import unittest
from pathlib import Path

from ci_esta.testing.test_policy import plan_workflow

ROOT = Path(__file__).resolve().parents[1]


class ProfileRoutingTests(unittest.TestCase):
    def test_real_workflows_route_profiles_without_copied_defaults(self):
        inventory = json.loads((ROOT / ".github/test-lanes.json").read_text())["lanes"]
        for workflow in ("ci.yml", "widget-store.yml"):
            for event, labels, changed, expected in (
                ("pull_request", [], ["library", "widget_comparison"], {"library"}),
                ("pull_request", ["test:affected-deep"], ["widget_comparison"], {"widget_comparison"}),
                ("pull_request", ["test:affected-deep"], ["library"], {"library"}),
                ("schedule", [], [], set(inventory)),
            ):
                with self.subTest(workflow=workflow, event=event, labels=labels, changed=changed):
                    result = plan_workflow(
                        ROOT / ".github/workflows" / workflow,
                        "select-tests",
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
                            "steps": {
                                "select": {
                                    "outputs": {
                                        "lanes": json.dumps({name: name in changed for name in inventory}),
                                    }
                                }
                            },
                        },
                    )
                    self.assertEqual({item.work.id for item in result.runnable}, expected)
