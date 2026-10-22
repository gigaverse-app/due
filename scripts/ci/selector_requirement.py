"""Install the same immutable selector revision used by the workflow action."""

import re
from pathlib import Path


def requirement() -> str:
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
    pins = re.findall(
        r"uses: gigaverse-app/pytest-selective/\.github/actions/plan-lanes@([0-9a-f]{40})\b", workflow.read_text()
    )
    if len(pins) != 1:
        raise ValueError("CI must have exactly one immutable pytest-selective action pin")
    return f"pytest-selective @ git+https://github.com/gigaverse-app/pytest-selective@{pins[0]}"


if __name__ == "__main__":
    print(requirement())
