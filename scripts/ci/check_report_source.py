"""Admit only a completed weekly CI checkpoint for the README's checked-out revision."""

import argparse
import json
import re
import subprocess
from typing import Any


def api(endpoint: str, *, pages: bool = False) -> Any:
    command = ["gh", "api", endpoint]
    if pages:
        command.extend(["--paginate", "--jq", ".jobs | @json"])
    output = subprocess.check_output(command, text=True)
    return [json.loads(line) for line in output.splitlines()] if pages else json.loads(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--expected-attempt", type=int)
    args = parser.parse_args()
    if args.run_id <= 0 or not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("expected a positive run ID and full checked-out commit SHA")
    endpoint = f"repos/{args.repository}/actions/runs/{args.run_id}"
    run = api(endpoint)
    expected = {
        "id": args.run_id,
        "path": ".github/workflows/ci.yml",
        "event": "schedule",
        "head_branch": "main",
        "head_sha": args.revision,
        "status": "completed",
    }
    if any(run.get(key) != value for key, value in expected.items()) or any(
        (run.get(key) or {}).get("full_name") != args.repository for key in ("repository", "head_repository")
    ):
        parser.error(
            "report source must be a completed scheduled CI run on this repository's checked-out main revision"
        )
    attempt = run.get("run_attempt")
    if (
        type(attempt) is not int
        or attempt < 1
        or (args.expected_attempt is not None and attempt != args.expected_attempt)
    ):
        parser.error("source run attempt is missing or changed while downloading evidence")
    # Pin job outcomes to the admitted attempt, including when the API has
    # enough jobs to paginate. A failed contract job should render as failed;
    # an unrelated lint failure must not hide its available evidence.
    pages = api(f"{endpoint}/attempts/{attempt}/jobs?per_page=100", pages=True)
    jobs = [job for page in pages for job in page if job["name"] == "Execute tests (locked)"]
    if len(jobs) != 1 or jobs[0].get("status") != "completed":
        parser.error("expected exactly one completed locked contract execution job")
    outcome = jobs[0].get("conclusion")
    if outcome not in ("success", "failure", "cancelled", "skipped"):
        parser.error("contract execution job has no supported conclusion")
    print(f"outcome={outcome}")
    print(f"attempt={attempt}")


if __name__ == "__main__":
    main()
