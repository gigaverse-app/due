"""Use the workflow's selector action revision for real-Git routing checks."""

import re
from pathlib import Path


def requirement() -> str:
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
    pins = re.findall(
        r"uses: gigaverse-app/pytest-selective/actions/plan-lanes@([A-Za-z0-9._/-]+)", workflow.read_text()
    )
    if len(pins) != 1:
        raise ValueError("CI must have exactly one pytest-selective plan-lanes action reference")
    return f"pytest-selective @ git+https://github.com/gigaverse-app/pytest-selective.git@{pins[0]}"


if __name__ == "__main__":
    print(requirement())
