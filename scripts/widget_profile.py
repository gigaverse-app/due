"""Quality-gated diagnostic profiles; never use instrumented runs as benchmarks.

The child finishes the complete app journey, then asks py-spy to stop while the
traced root is still alive. This avoids py-spy's traced-child wait/reap race.
Only a validated results.json and stack capture can certify success.
"""

import argparse
import json
import os
import re
import runpy
import signal
import subprocess
import sys
import traceback
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import psutil

PY_SPY_VERSION = "0.4.2"


def role_for(frames: list[dict]) -> str:
    # A prefork child's inherited command still identifies the worker. Use the
    # innermost process frame so the controller isn't charged for its children.
    commands = [frame["name"] for frame in frames if frame["name"].startswith("process ")]
    command = commands[-1] if commands else ""
    if "widget_store.main" in command:
        return "dbos"
    if "django runserver" in command:
        return "due_web"
    if "celery" in command:
        return "due_worker" if "worker" in command else "due_beat"
    return "controller_or_supervisor"


def validate_capture(directory: Path, mode: str) -> dict:
    results = json.loads((directory / "results.json").read_text())
    assert results.get("complete") is True, "application journey did not complete"
    assert results.get("profiled") is True, "instrumented results must be labelled"
    assert set(results["arms"]) == {"due", "dbos"}, "both applications must finish"
    log = (directory / "profiler.log").read_text()
    assert "behind in sampling" not in log, "profiler fell behind"
    matches = re.findall(r"Samples: (\d+) Errors: (\d+)", log)
    assert len(matches) == 1, "missing or ambiguous profiler completion"
    count, errors = map(int, matches[0])
    assert errors == 0, f"profiler lost {errors} stack reads"
    document = json.loads((directory / f"{mode}.json").read_text())
    frames = document["shared"]["frames"]
    roles: Counter[str] = Counter()
    unattributed: Counter[str] = Counter()
    leaves: dict[str, Counter[str]] = defaultdict(Counter)
    inclusive: dict[str, Counter[str]] = defaultdict(Counter)
    for profile in document["profiles"]:
        assert profile["type"] == "sampled"
        for sample in profile["samples"]:
            stack = [frames[index] for index in sample]
            role = role_for(stack)
            named = [
                f"{frame.get('file', '')}:{frame.get('line', '')} {frame['name']}"
                for frame in stack
                if frame.get("file")
            ]
            if not named:
                # A native/exiting thread can have no Python frame. Account for
                # it explicitly; it cannot satisfy application coverage.
                unattributed[role] += 1
                continue
            roles[role] += 1
            leaves[role][named[-1]] += 1
            inclusive[role].update(set(named))
    assert sum(roles.values()) + sum(unattributed.values()) == count, "log and profile sample counts disagree"
    assert sum(unattributed.values()) <= count * 0.01, "too many unattributed thread samples"
    assert roles["due_web"] >= 20 and roles["due_worker"] >= 20, "missing Due web or worker coverage"
    assert roles["due_web"] + roles["due_worker"] >= 100, "insufficient Due samples"
    assert roles["dbos"] >= 100, "insufficient DBOS samples"
    return {
        "mode": mode,
        "samples": count,
        "errors": errors,
        "samples_by_role": dict(roles),
        "unattributed_samples_by_role": dict(unattributed),
        "top_self_stacks": {role: value.most_common(30) for role, value in leaves.items()},
        "top_inclusive_stacks": {role: value.most_common(30) for role, value in inclusive.items()},
        "scope": "CPU mode: active GIL-held Python stack samples, not total/native CPU. Wall mode: all-thread snapshots including idle waits, not additive elapsed time. Inclusive functions overlap. Instrumented durations are not benchmark evidence.",
    }


def child(benchmark_args: list[str]) -> None:
    parent = psutil.Process(os.getppid())
    assert parent.name() == "py-spy", "child mode must be launched by py-spy"
    try:
        sys.argv = ["scripts.widget_benchmark", *benchmark_args]
        runpy.run_module("scripts.widget_benchmark", run_name="__main__")
    except BaseException:
        traceback.print_exc()
    finally:
        # Do not exit and race py-spy's ptrace waiter. It flushes its output,
        # checks this still-live child, and kills it itself. Failed journeys
        # have no complete result and are rejected independently by the parent.
        parent.send_signal(signal.SIGINT)
        while True:
            signal.pause()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("cpu", "wall"), default="cpu")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("benchmark_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    forwarded = args.benchmark_args
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    directory = args.output.resolve()
    benchmark_args = [*forwarded, "--profiled", "--output", str(directory)]
    if args.child:
        child(benchmark_args)
        return
    directory.mkdir(parents=True, exist_ok=True)
    assert not (directory / "results.json").exists(), "use a fresh profile output directory"
    command = [
        "uvx",
        "--from",
        f"py-spy=={PY_SPY_VERSION}",
        "py-spy",
        "record",
        "--subprocesses",
        "--rate",
        "25",
        "--gil" if args.mode == "cpu" else "--idle",
        "--format",
        "speedscope",
        "--output",
        str(directory / f"{args.mode}.json"),
        "--",
        sys.executable,
        "-m",
        "scripts.widget_profile",
        "--child",
        "--output",
        str(directory),
        "--",
        *forwarded,
    ]
    started = datetime.now(UTC).isoformat()
    with (directory / "profiler.log").open("w") as log:
        with subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True) as process:
            try:
                assert process.wait(timeout=600) == 0, f"profiler failed; see {directory / 'profiler.log'}"
            except BaseException:
                # The app supervisor owns independent process groups. On a
                # collector timeout, clean the entire owned descendant tree.
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
    print(json.dumps({"mode": args.mode, "samples_by_role": summary["samples_by_role"], "errors": summary["errors"]}))


if __name__ == "__main__":
    main()
