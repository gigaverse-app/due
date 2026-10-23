"""Install the compatible selector series used by the workflow action."""

import re
from pathlib import Path


def requirement() -> str:
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
    pins = re.findall(
        r"uses: gigaverse-app/pytest-selective/\.github/actions/plan-lanes@v([0-9]+)\.([0-9]+)\b", workflow.read_text()
    )
    if len(pins) != 1:
        raise ValueError("CI must have exactly one pytest-selective minor-version action tag")
    major, minor = map(int, pins[0])
    return f"pytest-selective>={major}.{minor}.0,<{major}.{minor + 1}.0"


if __name__ == "__main__":
    print(requirement())
