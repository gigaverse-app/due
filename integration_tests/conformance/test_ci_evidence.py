"""A passing profile report must not hide omitted application histories."""

import json
from pathlib import Path

import pytest
from pytest_obligation.profiles.harmless_replay import REPLAY_SAFETY_PROOFS

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


@pytest.mark.parametrize("required", ["delivery", "process publication"])
def test_ci_requires_a_named_process_proof_even_if_its_binding_is_deleted(tmp_path: Path, required: str) -> None:
    path = tmp_path / "effects.json"
    path.write_text(
        json.dumps(
            {
                "example": {
                    "expected": ["delivery"],
                    "collected": ["delivery"],
                    "selected": ["delivery"],
                    "unassessed": [],
                    "outcomes": {"delivery": ["passed"]},
                }
            }
        )
    )
    if required == "delivery":
        check_effects(path, ["example"], required=[required])
    else:
        with pytest.raises(AssertionError, match="missing required proof"):
            check_effects(path, ["example"], required=[required])


@pytest.mark.parametrize("missing", [None, *range(len(REPLAY_SAFETY_PROOFS))])
def test_ci_requires_every_canonical_replay_proof(tmp_path: Path, missing: int | None) -> None:
    proofs = [f"test_due_work_contract[REPLAY_SAFE_EXECUTION-{proof.__name__}]" for proof in REPLAY_SAFETY_PROOFS]
    if missing is not None:
        proofs.pop(missing)
    path = tmp_path / "effects.json"
    path.write_text(
        json.dumps(
            {
                "example": {
                    "expected": proofs,
                    "collected": proofs,
                    "selected": proofs,
                    "unassessed": [],
                    "outcomes": {proof: ["passed"] for proof in proofs},
                }
            }
        )
    )
    if missing is None:
        check_effects(path, ["example"], replay=True)
    else:
        with pytest.raises(AssertionError, match="missing required proof"):
            check_effects(path, ["example"], replay=True)
