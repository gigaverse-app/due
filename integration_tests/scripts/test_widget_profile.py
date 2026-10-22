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


@pytest.mark.parametrize("field", ["self_seconds", "inclusive_seconds"])
def test_negative_diagnostic_timing_preserves_the_usable_capture(tmp_path, field):
    capture(tmp_path)
    path = tmp_path / "functions/10.json"
    profile = json.loads(path.read_text())
    # Observed in real CPU artifacts: one Celery proxy row among thousands of
    # valid functions, after both application journeys completed successfully.
    anomaly = dict(
        name="run_durable_work_task.__getattr__",
        module="/app/celery/local.py",
        lineno=140,
        calls=2,
        self_seconds=0.0,
        inclusive_seconds=0.0,
        thread=11,
    )
    anomaly[field] = -0.006129379000000001
    profile["functions"].append(anomaly)
    path.write_text(json.dumps(profile))
    original = path.read_bytes()

    summary = validate_capture(tmp_path, "cpu")

    assert path.read_bytes() == original  # Preserve the original evidence, never clamp it.
    assert summary["timing_anomalies"] == [{"pid": 10, "role": "due_web", "function": anomaly}]
    assert summary["business_calls_by_role"] == {"due_web": 20, "due_worker": 20, "dbos": 20}
    assert summary["calls_by_role"] == {"due_web": 20, "due_worker": 40, "dbos": 20}
    for ranking in ("top_self_seconds", "top_inclusive_seconds"):
        assert not any("__getattr__" in name for name, _ in summary[ranking]["due_web"])


def test_a_process_with_only_invalid_timing_remains_unusable(tmp_path):
    capture(tmp_path)
    path = tmp_path / "functions/11.json"
    profile = json.loads(path.read_text())
    profile["functions"][0]["inclusive_seconds"] = -0.01
    path.write_text(json.dumps(profile))
    # A child has business coverage, but cannot stand in for its missing parent profile.
    with pytest.raises(AssertionError):
        validate_capture(tmp_path, "cpu")


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
        "negative_inclusive_time",
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
        elif fault == "negative_inclusive_time":
            value["functions"][0]["inclusive_seconds"] = -1
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


def test_profiler_injection_does_not_shadow_application_dependencies(tmp_path):
    import os
    import subprocess
    import sys

    from scripts.widget_profile import profiler_libraries

    libraries = profiler_libraries(tmp_path / "profiler")
    application = tmp_path / "application"
    application.mkdir()
    (application / "psycopg.py").write_text("APPLICATION_MARKER = 'original app driver'\n")
    result = subprocess.run(
        [sys.executable, "-S", "-c", "import yappi, psycopg; print(psycopg.APPLICATION_MARKER)"],
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(libraries), str(application)))},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "original app driver"


@pytest.mark.parametrize("mode", ["cpu", "wall"])
def test_flat_capture_preserves_every_public_function_counter(mode):
    """Compare collectors on one frozen real profile, not two noisy runs."""
    import subprocess
    import sys

    program = r"""
import json, threading, yappi
from scripts.widget_profile_bootstrap.sitecustomize import collect_functions

def work():
    sum(range(200))

yappi.set_clock_type(MODE)
yappi.set_context_name_callback(lambda: threading.current_thread().name)
yappi.start(builtins=True, profile_threads=True)
work()
# Distinct code objects sharing a source identity are merged by public Yappi.
for _ in range(4):
    exec(compile("(lambda: sum(range(10)))()", "generated.py", "exec"))
threads = [threading.Thread(target=work) for _ in range(12)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
yappi.stop()
expected = []
for thread in yappi.get_thread_stats():
    for function in yappi.get_func_stats(filter={"ctx_id": thread.id}):
        expected.append(dict(name=function.name, module=function.module,
            lineno=function.lineno, calls=function.ncall,
            self_seconds=function.tsub, inclusive_seconds=function.ttot, thread=thread.id))
actual = collect_functions()
key = lambda row: (row["thread"], row["module"], row["lineno"], row["name"])
assert sorted(actual, key=key) == sorted(expected, key=key)
assert sum(row["calls"] for row in actual if row["name"] == "work") == 13
assert any(row["thread"] == 0 for row in actual)
print(json.dumps({"functions": len(actual)}))
""".replace("MODE", repr(mode))
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["functions"] > 100


def test_periodic_snapshot_keeps_profiling_after_collection(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    bootstrap = Path(__file__).resolve().parents[2] / "scripts/widget_profile_bootstrap"
    program = r"""
import json, os, time
from pathlib import Path

def work():
    sum(range(200))

work()
path = Path(os.environ["WIDGET_PROFILE_OUTPUT"]) / f"{os.getpid()}.json"
deadline = time.monotonic() + 10
while not path.exists():
    assert time.monotonic() < deadline, "periodic snapshot never arrived"
    time.sleep(0.02)
rows = json.loads(path.read_text())["functions"]
assert sum(row["calls"] for row in rows if row["name"] == "work") == 1
assert not any(row["name"] == "background" for row in rows)
work()
print(os.getpid())
"""
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
    rows = json.loads((tmp_path / f"{int(result.stdout)}.json").read_text())["functions"]
    assert sum(row["calls"] for row in rows if row["name"] == "work") == 2
    assert not any(row["name"] == "background" for row in rows)
    assert not list(tmp_path.glob("error-*"))
