"""Profile both real apps with Yappi; instrumented times are not benchmarks.

Run with ``uv run python -m scripts.widget_profile``.
The opt-in bootstrap covers threads, prefork children and crash restarts without
ptrace. Periodic snapshots retain evidence across SIGKILL; their final second
can be absent. Application completion and business-function coverage are gated.
"""

import argparse
import importlib.metadata
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
BUSINESS_FUNCTIONS = {
    "due_web": ("/examples/widget_store/services.py", "checkout"),
    "due_worker": ("/examples/widget_store/services.py", "advance"),
    "dbos": ("/widget_store/main.py", "dispatch_order_workflow"),
}


def validate_capture(directory: Path, mode: str) -> dict:
    results = json.loads((directory / "results.json").read_text())
    assert results.get("complete") is True, "application journey did not complete"
    assert results.get("profiled") is True, "instrumented results must be labelled"
    assert set(results["arms"]) == {"due", "dbos"}, "both applications must finish"
    assert not list((directory / "functions").glob("error-*")), "profiler snapshot failed"
    profiles = [json.loads(path.read_text()) for path in (directory / "functions").glob("*.json")]
    assert profiles, "missing function profiles"
    by_pid = {profile["pid"]: profile for profile in profiles}
    assert len(by_pid) == len(profiles), "duplicate process profiles"
    roots = {}
    for arm in ("due", "dbos"):
        starts = re.findall(r"WIDGET_STARTED pids=(\[[^\]]+\])", (directory / f"{arm}.log").read_text())
        assert len(starts) >= 2, f"{arm}: missing crash restart"
        roles = ("due_web", "due_worker", "due_beat") if arm == "due" else ("dbos",)
        for start in starts:
            pids = json.loads(start)
            assert len(pids) in ((2, 3) if arm == "due" else (1,)), "unexpected app process layout"
            for pid, role in zip(pids, roles, strict=False):
                assert pid in by_pid, f"missing profile for {role} process {pid}"
                assert by_pid[pid]["role"] == role, "process role mismatch"
                roots[pid] = role
    business = Counter()
    generations = Counter()
    calls = Counter()
    timing_anomalies = []
    own = defaultdict(Counter)
    inclusive = defaultdict(Counter)
    for profile in profiles:
        ancestor = profile["pid"]
        visited = set()
        while ancestor not in roots:
            assert ancestor in by_pid and ancestor not in visited, "orphan or cyclic process ancestry"
            visited.add(ancestor)
            ancestor = by_pid[ancestor]["parent_pid"]
        assert profile["role"] == roots[ancestor], "descendant role mismatch"
        assert profile["clock"] == mode, "profile clock mismatch"
        assert profile["captured"] >= profile["started"], "invalid snapshot timestamps"
        role = profile["role"]
        assert role in {"due_web", "due_worker", "due_beat", "dbos"}, "unknown process role"
        assert profile["functions"], "empty process profile"
        usable_rows = 0
        for function in profile["functions"]:
            count, self_time, total = function["calls"], function["self_seconds"], function["inclusive_seconds"]
            assert count > 0 and all(math.isfinite(t) for t in (self_time, total)), "invalid function statistics"
            if self_time < 0 or total < 0:
                # Live Yappi snapshots can contain a negative inclusive counter.
                # Preserve the raw evidence and disclose the unusable row instead
                # of inventing a zero or discarding thousands of valid functions.
                # It cannot contribute timing, calls, or business-coverage proof.
                timing_anomalies.append({"pid": profile["pid"], "role": role, "function": function})
                continue
            usable_rows += 1
            key = f"{function['module']}:{function['lineno']} {function['name']}"
            own[role][key] += self_time
            inclusive[role][key] += total
            calls[role] += count
            expected = BUSINESS_FUNCTIONS.get(role)
            if expected and function["module"].endswith(expected[0]) and function["name"] == expected[1]:
                business[role] += count
                generations[ancestor] += count
            if (
                role == "due_beat"
                and function["module"].endswith("/celery/beat.py")
                and function["name"].endswith(".tick")
            ):
                generations[ancestor] += count
        assert usable_rows > 0, f"process {profile['pid']}: no usable function timing"
    assert all(business[role] > 0 for role in BUSINESS_FUNCTIONS), "missing business-function coverage"
    assert all(generations[pid] > 0 for pid in roots), "missing business coverage in a crash generation"
    return {
        "collector": "yappi 1.7.6",
        "mode": mode,
        "processes_by_role": dict(Counter(p["role"] for p in profiles)),
        "business_calls_by_role": dict(business),
        "calls_by_role": dict(calls),
        "timing_anomalies": timing_anomalies,
        "top_self_seconds": {role: counts.most_common(30) for role, counts in own.items()},
        "top_inclusive_seconds": {role: counts.most_common(30) for role, counts in inclusive.items()},
        "scope": "In-process function profiles, including builtins and application threads. CPU uses per-thread CPU clocks; wall includes waits and overlaps across threads. Writer thread excluded. Snapshots every second can omit the final interval of killed processes; scheduling delays can extend that interval. Active calls can have incomplete inclusive totals. Finite negative timing rows are preserved in raw files, listed in timing_anomalies and excluded from summary totals and coverage proof. No claim of complete call counts. Instrumentation changes timing and CPU cost; these are diagnostic profiles, not performance comparisons.",
    }


def profiler_libraries(directory: Path) -> Path:
    """Inject only Yappi, never another app's whole site-packages directory."""
    assert importlib.metadata.version("yappi") == "1.7.6", "use the pinned Yappi version"
    directory.mkdir()
    for name in ("yappi", "_yappi"):
        spec = importlib.util.find_spec(name)
        assert spec and spec.origin, "install locked development dependencies with uv sync --locked"
        source = Path(spec.origin)
        shutil.copy2(source, directory / source.name)
    return directory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("cpu", "wall"), default="cpu")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("benchmark_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    forwarded = args.benchmark_args[1:] if args.benchmark_args[:1] == ["--"] else args.benchmark_args
    directory = args.output.resolve()
    assert not directory.exists(), "use a fresh profile output directory"
    directory.mkdir(parents=True)
    libraries = profiler_libraries(directory / "profiler")
    command = [sys.executable, "-m", "scripts.widget_benchmark", *forwarded, "--profiled", "--output", str(directory)]
    env = {
        **os.environ,
        "WIDGET_PROFILE_OUTPUT": str(directory / "functions"),
        "WIDGET_PROFILE_CLOCK": args.mode,
        "PYTHONPATH": os.pathsep.join(
            [
                str(ROOT / "scripts/widget_profile_bootstrap"),
                str(libraries),
                os.environ.get("PYTHONPATH", ""),
            ]
        ),
    }
    started = datetime.now(UTC).isoformat()
    with (directory / "profiler.log").open("w") as log:
        with subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT) as process:
            try:
                assert process.wait(timeout=600) == 0, f"profiled journey failed; see {directory / 'profiler.log'}"
            except BaseException:
                if process.poll() is None:
                    owned = psutil.Process(process.pid).children(recursive=True)
                    for item in [*reversed(owned), psutil.Process(process.pid)]:
                        try:
                            item.terminate()
                        except psutil.NoSuchProcess:
                            pass
                    _, alive = psutil.wait_procs(owned, timeout=5)
                    for item in alive:
                        try:
                            item.kill()
                        except psutil.NoSuchProcess:
                            pass
                raise
    summary = validate_capture(directory, args.mode)
    summary.update(started_utc=started, finished_utc=datetime.now(UTC).isoformat(), command=command)
    (directory / "quality.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {
                **{key: summary[key] for key in ("mode", "processes_by_role", "business_calls_by_role")},
                "timing_anomaly_count": len(summary["timing_anomalies"]),
            }
        )
    )


if __name__ == "__main__":
    main()
