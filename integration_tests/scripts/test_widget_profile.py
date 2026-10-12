"""A profiler exit status alone must never certify an incomplete capture."""

import json
from copy import deepcopy

import pytest

from scripts.widget_profile import validate_capture

pytestmark = pytest.mark.no_db


def capture(tmp_path):
    frames = [
        {"name": 'process 2:"python -m django runserver"', "file": ""},
        {"name": 'process 3:"python -m celery worker --beat"', "file": ""},
        {"name": 'process 4:"python -m widget_store.main"', "file": ""},
        {"name": "work", "file": "/app/package/src/due/django/runtime/tasks.py", "line": 1},
    ]
    profile = {
        "shared": {"frames": frames},
        "profiles": [
            {"type": "sampled", "name": f"thread {i}", "samples": [[i, 3]] * count}
            for i, count in enumerate((40, 100, 100))
        ],
    }
    benchmark = {"complete": True, "profiled": True, "arms": {"due": {}, "dbos": {}}}
    (tmp_path / "results.json").write_text(json.dumps(benchmark))
    (tmp_path / "cpu.json").write_text(json.dumps(profile))
    (tmp_path / "profiler.log").write_text("Samples: 240 Errors: 0\n")
    return profile, benchmark


def test_complete_capture_has_both_applications_and_worker(tmp_path):
    capture(tmp_path)
    summary = validate_capture(tmp_path, "cpu")
    assert summary["samples_by_role"] == {"due_web": 40, "due_worker": 100, "dbos": 100}
    assert summary["errors"] == 0


@pytest.mark.parametrize(
    "fault",
    ["errors", "lag", "incomplete", "unprofiled", "missing_arm", "missing_worker", "sparse_dbos", "count_mismatch"],
)
def test_rejects_unusable_profiles(tmp_path, fault):
    profile, benchmark = capture(tmp_path)
    if fault == "errors":
        (tmp_path / "profiler.log").write_text("Samples: 240 Errors: 1\n")
    elif fault == "lag":
        (tmp_path / "profiler.log").write_text("1.31s behind in sampling\nSamples: 240 Errors: 0\n")
    elif fault in {"incomplete", "unprofiled", "missing_arm"}:
        if fault == "incomplete":
            benchmark["complete"] = False
        elif fault == "unprofiled":
            benchmark["profiled"] = False
        else:
            benchmark["arms"].pop("dbos")
        (tmp_path / "results.json").write_text(json.dumps(benchmark))
    else:
        profile = deepcopy(profile)
        if fault == "missing_worker":
            profile["shared"]["frames"][1]["name"] = 'process 3:"python -m scripts.widget_benchmark"'
        elif fault == "sparse_dbos":
            profile["profiles"][2]["samples"] = [[2, 3]] * 10
            (tmp_path / "profiler.log").write_text("Samples: 150 Errors: 0\n")
        else:
            profile["profiles"][2]["samples"].pop()
        (tmp_path / "cpu.json").write_text(json.dumps(profile))
    with pytest.raises(AssertionError):
        validate_capture(tmp_path, "cpu")


def test_unattributed_thread_sample_is_reported_and_does_not_inflate_coverage(tmp_path):
    profile, _ = capture(tmp_path)
    profile["profiles"][0]["samples"].append([0])
    (tmp_path / "cpu.json").write_text(json.dumps(profile))
    (tmp_path / "profiler.log").write_text("Samples: 241 Errors: 0\n")
    summary = validate_capture(tmp_path, "cpu")
    assert summary["samples_by_role"]["due_web"] == 40
    assert summary["unattributed_samples_by_role"] == {"due_web": 1}


def test_missing_python_frames_cannot_pass_as_application_coverage(tmp_path):
    profile, _ = capture(tmp_path)
    profile["profiles"][2]["samples"] = [[2]] * 100
    (tmp_path / "cpu.json").write_text(json.dumps(profile))
    with pytest.raises(AssertionError):
        validate_capture(tmp_path, "cpu")
