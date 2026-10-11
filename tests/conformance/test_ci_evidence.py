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
