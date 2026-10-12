# Adapted from gigaverse-app/pytest-obligation (Apache-2.0).
"""CI enrollment guard: a deleted or filtered adopter must not leave a green empty report."""

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from pydantic import TypeAdapter
from pytest_obligation.profiles.coverage import SuiteCoverage


def read_report(path: Path) -> dict[str, SuiteCoverage]:
    """Validate canonical report models and recompute verdicts instead of trusting stored booleans."""
    data = json.loads(path.read_text())
    for suite in data.values():
        for profile in suite["profiles"].values():
            profile.pop("verified", None)
            for case in profile["cases"].values():
                case.pop("passed", None)
    return TypeAdapter(dict[str, SuiteCoverage]).validate_python(data)


def check(
    reports: Mapping[str, SuiteCoverage], *, contracts: list[str], verified: list[str], exercised: list[str]
) -> None:
    """Require named adopters and completed proofs, keeping known failures distinct from verification."""
    for name in contracts:
        matching = [suite for suite in reports.values() if suite.name == name]
        assert len(matching) == 1, f"expected exactly one collected contract named {name!r}, found {len(matching)}"
        suite = matching[0]
        for letter in verified:
            assert suite.profiles[letter].verified, f"{name}/{letter}: expected complete passing profile evidence"
        for letter in exercised:
            cases = suite.profiles[letter].cases
            assert cases, f"{name}/{letter}: expected behavioral proofs, not just an assessment"
            for identity, case in cases.items():
                assert case.collected and case.selected, f"{name}/{identity}: proof was not selected"
                assert (
                    case.outcomes.get("setup") == ["passed"]
                    and case.outcomes.get("call") in (["passed"], ["xfail"])
                    and case.outcomes.get("teardown") == ["passed"]
                ), f"{name}/{identity}: proof did not finish successfully or as its declared failure"


def check_effects(
    path: Path,
    names: list[str],
    *,
    unassessed: tuple[str, ...] | list[str] = (),
    required: tuple[str, ...] | list[str] = (),
) -> None:
    """Require the whole generated adopter suite, including Due-specific histories."""
    reports = json.loads(path.read_text())
    for name in names:
        assert name in reports, f"missing work-table suite {name!r}"
        suite = reports[name]
        expected = set(suite["expected"])
        assert expected, f"{name}: no expected proofs"
        assert set(required) <= expected, f"{name}: missing required proof: {set(required) - expected}"
        assert set(suite["unassessed"]) == set(unassessed), f"{name}: unassessed obligations differ from declared debt"
        assert expected == set(suite["collected"]) == set(suite["selected"]), f"{name}: incomplete suite selection"
        assert set(suite["outcomes"]) == expected, f"{name}: missing executed proofs"
        for proof, outcomes in suite["outcomes"].items():
            required = ["xfail"] if proof in unassessed else ["passed"]
            assert outcomes == required, f"{name}/{proof}: expected {required}, got {outcomes}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--contract", action="append", required=True)
    parser.add_argument("--verified", nargs="+", default=[])
    parser.add_argument("--exercised", nargs="+", default=[])
    parser.add_argument("--effect-report", type=Path)
    parser.add_argument("--effect", action="append", default=[])
    parser.add_argument(
        "--unassessed",
        action="append",
        default=[],
        help="Exact expected binding-debt case; also enforce the pytest debt baseline",
    )
    parser.add_argument("--required-effect-case", action="append", default=[])
    arguments = parser.parse_args()
    check(
        read_report(arguments.report),
        contracts=arguments.contract,
        verified=arguments.verified,
        exercised=arguments.exercised,
    )
    if arguments.effect:
        assert arguments.effect_report is not None, "--effect requires --effect-report"
        check_effects(
            arguments.effect_report,
            arguments.effect,
            unassessed=arguments.unassessed,
            required=arguments.required_effect_case,
        )
    print(f"Validated executed adoption for {len(arguments.contract)} contract(s)")


if __name__ == "__main__":
    main()
