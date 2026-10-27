"""Build the reviewed provider candidate and smoke-test Due's installed testing extra.

The local version identifies a candidate, never a published obligation release.
Release CI separately resolves the extra exclusively from PyPI.
"""

import os
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(args, check=True, env=env)


def main() -> None:
    project = tomllib.loads((ROOT / "package/pyproject.toml").read_text())
    source = project["tool"]["uv"]["sources"]["pytest-obligation"]
    with tempfile.TemporaryDirectory(prefix="due-testing-wheel-") as temporary:
        work = Path(temporary)
        checkout = work / "obligation"
        run("git", "init", "-q", str(checkout))
        run("git", "-C", str(checkout), "fetch", "--depth=1", source["git"], source["rev"])
        run("git", "-C", str(checkout), "checkout", "--detach", "FETCH_HEAD")
        candidate = work / "candidate"
        run(
            "uv",
            "build",
            "--project",
            str(checkout / source["subdirectory"]),
            "--wheel",
            "--out-dir",
            str(candidate),
            env={**os.environ, "SETUPTOOLS_SCM_PRETEND_VERSION": f"0.23.0+candidate.{source['rev'][:12]}"},
        )
        environment = work / "venv"
        run("uv", "venv", str(environment))
        python = environment / "bin/python"
        wheel = next((ROOT / "dist").glob("*.whl"))
        run("uv", "pip", "install", "--python", str(python), f"{wheel}[testing]", str(next(candidate.glob("*.whl"))))
        run("uv", "pip", "check", "--python", str(python))
        run(str(python), "-I", str(ROOT / "scripts/check_testing_wheel.py"))


if __name__ == "__main__":
    main()
