"""A passing profile report must not hide omitted application histories."""

import json
from pathlib import Path

import pytest

from scripts.check_adoption_report import check_effects


@pytest.mark.parametrize(
    "fault", [None, "absent", "empty", "filtered", "missing", "skipped", "xfail", "duplicate", "unassessed"]
)
def test_ci_rejects_missing_or_nonpassing_application_evidence(tmp_path: Path, fault: str | None) -> None:
    suite = {
        "expected": ["receipt repair", "concurrent settlement"],
        "collected": ["receipt repair", "concurrent settlement"],
        "selected": ["receipt repair", "concurrent settlement"],
        "unassessed": [],
        "outcomes": {"receipt repair": ["passed"], "concurrent settlement": ["passed"]},
    }
    data = {"example": suite}
    if fault == "absent":
        data.clear()
    elif fault == "empty":
        suite["expected"] = []
    elif fault == "filtered":
        suite["selected"] = ["receipt repair"]
    elif fault == "missing":
        del suite["outcomes"]["concurrent settlement"]
    elif fault in {"skipped", "xfail"}:
        suite["outcomes"]["concurrent settlement"] = [fault]
    elif fault == "duplicate":
        suite["outcomes"]["concurrent settlement"] = ["passed", "passed"]
    elif fault == "unassessed":
        suite["unassessed"] = ["receipt repair"]
    path = tmp_path / "effects.json"
    path.write_text(json.dumps(data))
    if fault:
        with pytest.raises(AssertionError):
            check_effects(path, ["example"])
    else:
        check_effects(path, ["example"])


@pytest.mark.parametrize("fault", [None, "extra", "stale", "passed", "other_xfail"])
def test_ci_accepts_only_exact_declared_unassessed_cases(tmp_path: Path, fault: str | None) -> None:
    declined = "test_due_work_contract[E-not_assessed]"
    suite = {
        "expected": [declined, "receipt repair"],
        "collected": [declined, "receipt repair"],
        "selected": [declined, "receipt repair"],
        "unassessed": [declined],
        "outcomes": {declined: ["xfail"], "receipt repair": ["passed"]},
    }
    if fault == "extra":
        suite["unassessed"].append("receipt repair")
    elif fault == "stale":
        suite["unassessed"] = []
    elif fault == "passed":
        suite["outcomes"][declined] = ["passed"]
    elif fault == "other_xfail":
        suite["outcomes"]["receipt repair"] = ["xfail"]
    path = tmp_path / "effects.json"
    path.write_text(json.dumps({"example": suite}))
    if fault:
        with pytest.raises(AssertionError):
            check_effects(path, ["example"], unassessed=[declined])
    else:
        check_effects(path, ["example"], unassessed=[declined])
