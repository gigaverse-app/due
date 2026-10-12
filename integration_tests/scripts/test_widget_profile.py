"""Profile success requires real business coverage across crash generations."""

import json

import pytest

from scripts.widget_profile import validate_capture

pytestmark = pytest.mark.no_db


def capture(tmp_path):
    (tmp_path / "results.json").write_text(
        json.dumps({"complete": True, "profiled": True, "arms": {"due": {}, "dbos": {}}})
    )
    (tmp_path / "due.log").write_text("WIDGET_STARTED pids=[10, 11]\nWIDGET_STARTED pids=[20, 21]\n")
    (tmp_path / "dbos.log").write_text("WIDGET_STARTED pids=[30]\nWIDGET_STARTED pids=[40]\n")
    directory = tmp_path / "functions"
    directory.mkdir()
    for pid, parent, role, name, module in (
        (10, 1, "due_web", "checkout", "/app/examples/widget_store/services.py"),
        (11, 1, "due_worker", "startup", "/app/celery/worker.py"),
        (12, 11, "due_worker", "advance", "/app/examples/widget_store/services.py"),
        (20, 1, "due_web", "checkout", "/app/examples/widget_store/services.py"),
        (21, 1, "due_worker", "startup", "/app/celery/worker.py"),
        (22, 21, "due_worker", "advance", "/app/examples/widget_store/services.py"),
        (30, 1, "dbos", "dispatch_order_workflow", "/upstream/widget_store/main.py"),
        (40, 1, "dbos", "dispatch_order_workflow", "/upstream/widget_store/main.py"),
    ):
        (directory / f"{pid}.json").write_text(
            json.dumps(
                {
                    "pid": pid,
                    "parent_pid": parent,
                    "role": role,
                    "clock": "cpu",
                    "started": 1,
                    "captured": 2,
                    "functions": [
                        {
                            "name": name,
                            "module": module,
                            "lineno": 1,
                            "calls": 10,
                            "self_seconds": 0.01,
                            "inclusive_seconds": 0.02,
                        }
                    ],
                }
            )
        )


def test_complete_capture_covers_business_calls_before_and_after_crash(tmp_path):
    capture(tmp_path)
    summary = validate_capture(tmp_path, "cpu")
    assert summary["business_calls_by_role"] == {"due_web": 20, "due_worker": 20, "dbos": 20}
    assert summary["processes_by_role"] == {"due_web": 2, "due_worker": 4, "dbos": 2}


@pytest.mark.parametrize(
    "fault",
    [
        "incomplete",
        "unprofiled",
        "missing_arm",
        "missing_process",
        "missing_restart",
        "writer_error",
        "wrong_clock",
        "wrong_role",
        "empty",
        "missing_business",
        "missing_second_generation",
        "orphan",
        "negative_time",
        "nan_time",
        "negative_calls",
        "bad_timestamp",
    ],
)
def test_rejects_unusable_profiles(tmp_path, fault):
    capture(tmp_path)
    if fault in {"incomplete", "unprofiled", "missing_arm"}:
        path = tmp_path / "results.json"
        value = json.loads(path.read_text())
        if fault == "missing_arm":
            value["arms"].pop("dbos")
        else:
            value["complete" if fault == "incomplete" else "profiled"] = False
        path.write_text(json.dumps(value))
    elif fault == "missing_process":
        (tmp_path / "functions/11.json").unlink()
    elif fault == "missing_restart":
        (tmp_path / "dbos.log").write_text("WIDGET_STARTED pids=[30]\n")
    elif fault == "writer_error":
        (tmp_path / "functions/error-10.txt").write_text("disk full")
    else:
        path = tmp_path / (
            "functions/22.json" if fault in {"missing_second_generation", "orphan"} else "functions/10.json"
        )
        value = json.loads(path.read_text())
        if fault == "wrong_clock":
            value["clock"] = "wall"
        elif fault == "wrong_role":
            value["role"] = "dbos"
        elif fault == "empty":
            value["functions"] = []
        elif fault in {"missing_business", "missing_second_generation"}:
            value["functions"][0]["name"] = "startup"
        elif fault == "orphan":
            value["parent_pid"] = 999
        elif fault == "negative_time":
            value["functions"][0]["self_seconds"] = -1
        elif fault == "nan_time":
            value["functions"][0]["self_seconds"] = float("nan")
        elif fault == "negative_calls":
            value["functions"][0]["calls"] = -1
        elif fault == "bad_timestamp":
            value["captured"] = 0
        path.write_text(json.dumps(value))
    with pytest.raises(AssertionError):
        validate_capture(tmp_path, "cpu")


@pytest.mark.parametrize("fork", [False, True])
def test_real_profiler_keeps_threads_and_fork_generations_separate(tmp_path, fork):
    import os
    import subprocess
    import sys
    from pathlib import Path

    if fork and not hasattr(os, "fork"):
        pytest.skip("prefork application profiling requires POSIX")
    bootstrap = Path(__file__).resolve().parents[2] / "scripts/widget_profile_bootstrap"
    program = """
import json, os, threading

def work():
    sum(range(100))

def child_only():
    sum(range(200))

work()
thread = threading.Thread(target=work)
thread.start()
thread.join()
parent = os.getpid()
child = os.fork() if FORK else None
if child == 0:
    child_only()
else:
    if child:
        os.waitpid(child, 0)
    print(json.dumps({"parent": parent, "child": child}))
""".replace("FORK", repr(fork))
    result = subprocess.run(
        [sys.executable, "-c", program, "celery", "worker"],
        env={
            **os.environ,
            "PYTHONPATH": str(bootstrap),
            "WIDGET_PROFILE_OUTPUT": str(tmp_path),
            "WIDGET_PROFILE_CLOCK": "cpu",
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    identities = json.loads(result.stdout)
    parent = json.loads((tmp_path / f"{identities['parent']}.json").read_text())
    assert sum(f["calls"] for f in parent["functions"] if f["name"] == "work") == 2
    assert not any(f["name"] in {"child_only", "background"} for f in parent["functions"])
    assert not list(tmp_path.glob("error-*"))
    if fork:
        child = json.loads((tmp_path / f"{identities['child']}.json").read_text())
        assert child["parent_pid"] == parent["pid"]
        assert sum(f["calls"] for f in child["functions"] if f["name"] == "child_only") == 1
        assert not any(f["name"] == "work" for f in child["functions"])
