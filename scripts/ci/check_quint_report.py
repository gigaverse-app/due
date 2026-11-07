"""Run pinned Quint checks and implementation replay; retain failure traces."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "test-results/quint-report"
QUINT = ROOT / "node_modules/.bin/quint"
DOCS = ROOT / "docs/quint/report-provenance"


def run(name, args, *, counterexample=False):
    result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (OUT / f"{name}.log").write_text(result.stdout)
    if counterexample:
        ok = result.returncode != 0 and "Invariant violated" in result.stdout
    else:
        ok = result.returncode == 0
    if not ok:
        raise RuntimeError(f"{name} failed (exit {result.returncode}); see {OUT / (name + '.log')}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "failure.txt").unlink(missing_ok=True)
    if not QUINT.is_file():
        raise RuntimeError(f"missing pinned Quint executable: {QUINT}; run npm ci")
    q = str(QUINT)
    version = subprocess.check_output([q, "--version"], cwd=ROOT, text=True).strip()
    if version != "0.33.1":
        raise RuntimeError(f"expected Quint 0.33.1, got {version}")
    paths = [
        DOCS / "audit.qnt",
        DOCS / "fixed.qnt",
        ROOT / "scripts/ci/check_report_source.py",
        ROOT / ".github/workflows/obligation-report.yml",
        ROOT / "scripts/ci/replay_report_provenance.py",
        ROOT / "ci_tests/test_report_source.py",
        ROOT / "package-lock.json",
        ROOT / "package.json",
        ROOT / ".github/workflows/ci.yml",
        ROOT / ".github/test-lanes.json",
        ROOT / "scripts/ci/check_quint_report.py",
        ROOT / "ci_tests/test_routing.py",
    ]
    manifest = {
        "quint": version,
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "seed": 1,
        "random_replay_seed": 22,
        "random_replay_traces": 20,
        "simulation_samples": 1000,
        "max_steps": 12,
        "sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    audit = str(DOCS / "audit.qnt")
    fixed = str(DOCS / "fixed.qnt")
    for name, model in (("audit", audit), ("fixed", fixed)):
        run(f"{name}-typecheck", [q, "typecheck", model])
    common = ["--backend", "typescript", "--seed", "1"]
    run("fixed-scenarios", [q, "test", fixed, "--main", "report_provenance_fixed", *common, "--max-samples", "1"])
    run(
        "fixed-simulation",
        [
            q,
            "run",
            fixed,
            "--main",
            "report_provenance_fixed",
            *common,
            "--max-samples",
            "1000",
            "--max-steps",
            "12",
            "--invariant",
            "readmeProvenanceHonest",
            "--out-itf",
            str(OUT / "fixed-counterexample-{seq}.itf.json"),
        ],
    )
    run(
        "replay-model-simulation",
        [
            q,
            "run",
            fixed,
            "--main",
            "report_replay",
            *common,
            "--max-samples",
            "1000",
            "--max-steps",
            "12",
            "--invariant",
            "safety",
            "--out-itf",
            str(OUT / "replay-model-counterexample-{seq}.itf.json"),
        ],
    )
    run(
        "audit-counterexample",
        [
            q,
            "run",
            audit,
            "--main",
            "manual_report_dispatch",
            *common,
            "--max-samples",
            "1000",
            "--max-steps",
            "12",
            "--invariant",
            "readmeProvenanceHonest",
            "--out-itf",
            str(OUT / "audit-counterexample-{seq}.itf.json"),
        ],
        counterexample=True,
    )
    run(
        "replay",
        [sys.executable, str(ROOT / "scripts/ci/replay_report_provenance.py"), "--quint", q, "--mutation-check"],
    )
    print("Quint report provenance checks and real shell replay passed; artifacts in", OUT)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "failure.txt").write_text(f"{type(error).__name__}: {error}\n")
        raise
