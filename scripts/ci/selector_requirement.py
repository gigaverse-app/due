"""Use the workflow's planner action revision for real-Git routing checks."""

import re
from pathlib import Path


def requirement() -> str:
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
    pins = re.findall(r"uses: gigaverse-app/ci-esta/actions/plan-work@([A-Za-z0-9._/-]+)", workflow.read_text())
    if len(pins) != 1:
        raise ValueError("CI must have exactly one CI-esta plan-work action reference")
    return f"ci-esta[selection] @ git+https://github.com/gigaverse-app/ci-esta.git@{pins[0]}"


if __name__ == "__main__":
    print(requirement())
