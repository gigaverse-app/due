"""Install the reviewed verifier from pinned upstream source and a local patch."""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "tooling/source-contracts"


def require(condition: object, message: object) -> None:
    if not condition:
        raise RuntimeError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_digest(root: Path) -> str:
    files = {path.relative_to(root).as_posix(): digest(path) for path in sorted(root.rglob("*.py"))}
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upstream-checkout", type=Path, help="Local pinned upstream Git object source, for offline development"
    )
    parser.add_argument("--wheel", type=Path, help="Optional wheel whose SHA256 is enrolled in verifier.json")
    args = parser.parse_args()
    config = json.loads((INPUTS / "verifier.json").read_text())
    require(config["version"] == 1, "source-contract integrity check failed")
    patch = INPUTS / "verifier.patch"
    lock = INPUTS / "requirements-project.lock"
    build_lock = INPUTS / "requirements-build.lock"
    require(digest(patch) == config["patch_sha256"], "unreviewed verifier patch")
    require(digest(lock) == config["requirements_sha256"], "unreviewed verifier dependencies")
    require(digest(build_lock) == config["build_requirements_sha256"], "unreviewed verifier build dependencies")
    require(len(config["commit"]) == 40 and config["license"] == "MIT", "source-contract integrity check failed")
    output = ROOT / "test-results/source-verifier"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="due-verifier-") as temporary:
        checkout = Path(temporary) / "source"
        checkout.mkdir()
        run("git", "init", "-q", str(checkout))
        origin = str(args.upstream_checkout.resolve()) if args.upstream_checkout else config["upstream"]
        run("git", "fetch", "--quiet", "--depth=1", origin, config["commit"], cwd=checkout)
        require(run("git", "rev-parse", "FETCH_HEAD", cwd=checkout) == config["commit"], "wrong upstream commit")
        run("git", "checkout", "--quiet", "--detach", "FETCH_HEAD", cwd=checkout)
        run("git", "apply", "--check", str(patch), cwd=checkout)
        run("git", "apply", str(patch), cwd=checkout)
        expected_tree = source_digest(checkout / "veripy")
        require(expected_tree == config["source_tree_sha256"], "patched compiler source differs from reviewed tree")
        if args.wheel:
            wheel = args.wheel.resolve()
            require(digest(wheel) == config["wheel_sha256"], "unreviewed verifier wheel")
        else:
            wheels = Path(temporary) / "wheels"
            run(
                "uv",
                "build",
                "--wheel",
                "--out-dir",
                str(wheels),
                "--build-constraints",
                str(build_lock),
                "--require-hashes",
                str(checkout),
            )
            candidates = list(wheels.glob("*.whl"))
            require(len(candidates) == 1, "source-contract integrity check failed")
            wheel = candidates[0]
        wheel_hash = digest(wheel)
        run("uv", "pip", "install", "--python", sys.executable, "--reinstall", "--require-hashes", "-r", str(lock))
        run("uv", "pip", "install", "--python", sys.executable, "--reinstall", "--no-deps", str(wheel))
        # Isolated import prevents the bootstrap directory or editable checkout
        # from impersonating the installed compiler.
        installed = Path(
            run(
                sys.executable,
                "-I",
                "-c",
                "import pathlib,veripy; print(pathlib.Path(veripy.__file__).resolve().parent)",
            )
        )
        require(source_digest(installed) == expected_tree, "installed compiler does not match reviewed source")
        evidence = {**config, "installed": str(installed), "built_wheel_sha256": wheel_hash}
        (output / "provenance.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"Installed reviewed VeriPy source {config['source_tree_sha256']}")


if __name__ == "__main__":
    main()
