"""Replay Quint ITF states through the deployed workflow shell and validator."""

import argparse
import difflib
import importlib.util
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "docs/quint/report-provenance/fixed.qnt"
EXPECTED = {"valid", "bad_event", "bad_revision", "bad_job", "failed_job", "second_page", "rerun"}
FAULTS = {
    "valid": None,
    "bad_event": ("event", "pull_request"),
    "bad_revision": ("head_sha", "0" * 40),
    "bad_job": "job-missing",
    "failed_job": "job-failed",
    "second_page": "job-second-page",
    "rerun": None,
}


def load_boundary_tests():
    path = ROOT / "ci_tests/test_report_source.py"
    spec = importlib.util.spec_from_file_location("report_boundary", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replay(traces, boundary, *, require_all=True):
    seen = set()
    failures = []
    for path in sorted(traces.glob("*.itf.json")):
        trace = json.loads(path.read_text())
        states = [item["s"] for item in trace["states"]]
        scenario = states[1]["scenario"]
        if scenario not in EXPECTED:
            raise RuntimeError(f"unknown scenario in {path}: {scenario}")
        seen.add(scenario)
        last = states[-1]
        if last["phase"] not in {"ready", "rejected"} or any(
            state["scenario"] not in {"none", scenario} for state in states
        ):
            raise RuntimeError(f"incomplete or inconsistent generated trace: {path}")
        admitted = any(state["phase"] == "download" and state["admitted"] for state in states)
        result, output = boundary.run_step(FAULTS[scenario])
        observed = result.returncode == 0
        if observed != admitted:
            failures.append(f"{scenario}: admission expected {admitted}, got {observed}: {result.stderr}")
            continue
        if not admitted:
            if "outcome=" in output:
                failures.append(f"{scenario}: rejected source emitted an outcome")
            continue
        if f"outcome={last['outcome']}\n" not in output or "attempt=1\n" not in output:
            failures.append(f"{scenario}: output {output!r}, expected {last['outcome']} and attempt 1")
        # The real post-download shell uses the originally admitted attempt.
        recheck_fault = ("run_attempt", 2) if scenario == "rerun" else None
        recheck, _ = boundary.run_step(recheck_fault, recheck=True)
        if (recheck.returncode == 0) != last["render"]:
            failures.append(f"{scenario}: render eligibility disagrees with recheck: {recheck.stderr}")
    if not seen or (require_all and seen != EXPECTED):
        raise RuntimeError(f"empty or missing replay scenarios: {sorted(EXPECTED - seen)}")
    # Exercise the deployed YAML ordering, not a duplicate ordering predicate.
    boundary.test_admission_brackets_downloads_before_rendering()
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quint", default="node_modules/.bin/quint")
    parser.add_argument("--artifacts", default="test-results/quint-report")
    parser.add_argument("--mutation-check", action="store_true")
    args = parser.parse_args()
    artifacts = ROOT / args.artifacts
    artifacts.mkdir(parents=True, exist_ok=True)
    traces = artifacts / "traces"
    shutil.rmtree(traces, ignore_errors=True)
    traces.mkdir()
    command = [
        args.quint,
        "test",
        str(MODEL),
        "--main",
        "report_replay",
        "--backend",
        "typescript",
        "--seed",
        "1",
        "--max-samples",
        "1",
        "--out-itf",
        str(traces / "{test}_{seq}.itf.json"),
    ]
    with (artifacts / "generator.log").open("w") as log:
        subprocess.run(command, cwd=ROOT, check=True, stdout=log, stderr=subprocess.STDOUT)
    random_traces = artifacts / "random-traces"
    shutil.rmtree(random_traces, ignore_errors=True)
    random_traces.mkdir()
    random_command = [
        args.quint,
        "run",
        str(MODEL),
        "--main",
        "report_replay",
        "--backend",
        "typescript",
        "--seed",
        "22",
        "--max-samples",
        "20",
        "--max-steps",
        "4",
        "--n-traces",
        "20",
        "--out-itf",
        str(random_traces / "random-{seq}.itf.json"),
        "--verbosity",
        "0",
    ]
    with (artifacts / "random-generator.log").open("w") as log:
        subprocess.run(random_command, cwd=ROOT, check=True, stdout=log, stderr=subprocess.STDOUT)
    if len(list(random_traces.glob("*.itf.json"))) != 20:
        raise RuntimeError("random generator did not emit 20 traces")
    boundary_module = load_boundary_tests()
    boundary = boundary_module.ReportSourceTests()
    failures = replay(traces, boundary) + replay(random_traces, boundary, require_all=False)
    if failures:
        (artifacts / "replay-failures.txt").write_text("\n".join(failures) + "\n")
        raise SystemExit("\n".join(failures))
    if args.mutation_check:
        # Mutate a disposable copy of the real validator and execute the same
        # real workflow shell; the production checkout is never modified.
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".quint-mutant-") as temp:
            mutant = Path(temp)
            for name in (".github/workflows/obligation-report.yml", "scripts/ci/check_report_source.py"):
                dest = mutant / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / name, dest)
            validator = mutant / "scripts/ci/check_report_source.py"
            before = validator.read_text()
            after = before.replace('        "event": "schedule",\n', "")
            if before == after:
                raise RuntimeError("mutation target missing; update the mutation control")
            validator.write_text(after)
            (artifacts / "mutation.patch").write_text(
                "".join(
                    difflib.unified_diff(
                        before.splitlines(keepends=True),
                        after.splitlines(keepends=True),
                        fromfile="a/scripts/ci/check_report_source.py",
                        tofile="b/scripts/ci/check_report_source.py",
                    )
                )
            )
            boundary_module.__dict__["ROOT"] = mutant
            detected = replay(traces, boundary)
            expected = "bad_event: admission expected False, got True: "
            if len(detected) != 1 or detected[0] != expected:
                raise RuntimeError(f"mutation did not produce precisely the expected weakening mismatch: {detected}")
            (artifacts / "mutation-control.txt").write_text("\n".join(detected) + "\n")
    print(
        f"Replayed {len(EXPECTED)} named histories (seed 1) and 20 sampled histories (seed 22) against deployed shell"
    )


if __name__ == "__main__":
    main()
