"""Verify the installed testing extra using only published dependencies."""

import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="due-testing-wheel-") as temporary:
        environment = Path(temporary) / "venv"
        subprocess.run(["uv", "venv", str(environment)], check=True)
        python = environment / "bin/python"
        wheel = next((ROOT / "dist").glob("*.whl"))
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--no-sources",
                "--default-index",
                "https://pypi.org/simple",
                "--python",
                str(python),
                f"{wheel}[testing]",
            ],
            check=True,
        )
        subprocess.run(["uv", "pip", "check", "--python", str(python)], check=True)
        subprocess.run([str(python), "-I", str(ROOT / "scripts/check_testing_wheel.py")], check=True)


if __name__ == "__main__":
    main()
