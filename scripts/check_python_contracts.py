"""Require source proofs, native PostgreSQL effects, enrollment and fault controls.

Run with the installed reviewed VeriPy wheel and Dafny 4.11.0 on PATH.
The compiler proves local source contracts. Native tests and existing generated
application contracts separately check the Django/PostgreSQL execution boundary.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "package/src/due"
REQUIRED_CHECKS = {"source", "native", "generated", "mutations"}


def require(condition: object, message: object) -> None:
    if not condition:
        raise RuntimeError(message)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def run(
    name: str, args: list[str], output: Path, *, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (output / f"{name}.log").write_text(result.stdout)
    write_json(output / f"{name}.command.json", {"argv": args, "cwd": str(cwd), "returncode": result.returncode})
    return result


def junit_cases(path: Path) -> dict[str, ET.Element]:
    root = ET.parse(path).getroot()
    cases: dict[str, ET.Element] = {}
    for case in root.iter("testcase"):
        identity = f"{case.attrib['classname']}::{case.attrib['name']}"
        require(identity not in cases, f"duplicate execution: {identity}")
        require(case.find("error") is None, f"test infrastructure failed: {identity}")
        require(case.find("skipped") is None, f"required test skipped: {identity}")
        cases[identity] = case
    require(cases, "zero executed tests")
    return cases


def require_native_inventory(path: Path, required: list[str]) -> None:
    require(required and len(required) == len(set(required)), "empty or duplicate required native cases")
    cases = junit_cases(path)
    native = {key for key in cases if key.startswith("tests.source_contracts.")}
    require(
        native == set(required),
        f"native enrollment differs: missing={set(required) - native}, unexpected={native - set(required)}",
    )
    require(all(case.find("failure") is None for case in cases.values()), "required native/generated test failed")


def require_generated_inventory(path: Path, suite: str, required: list[str]) -> None:
    from scripts.check_adoption_report import check_effects

    require(required and len(required) == len(set(required)), "empty or duplicate required generated cases")
    report = json.loads(path.read_text())
    require(set(report) == {suite}, "generated suite enrollment differs")
    require(set(report[suite]["expected"]) == set(required), "generated proof enrollment differs")
    check_effects(path, [suite], required=required)


def attestation(env: dict[str, str], expected: Path, output: Path, name: str) -> None:
    code = """import hashlib, json
from pathlib import Path
from due import claim_authority, retry_delays
from due.django.runtime import coordination, settlement_batch
modules = [claim_authority, retry_delays, coordination, settlement_batch]
print(json.dumps({m.__name__: {'path': str(Path(m.__file__).resolve()), 'sha256': hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()} for m in modules}))
"""
    settings = {**env, "DJANGO_SETTINGS_MODULE": "tests.settings"}
    code = "import django; django.setup()\n" + code
    result = run(name, [sys.executable, "-c", code], output, cwd=ROOT / "package", env=settings)
    require(result.returncode == 0, f"production import attestation failed; see {name}.log")
    imports = json.loads(result.stdout)
    for module, data in imports.items():
        relative = Path(*module.split(".")[1:]).with_suffix(".py")
        actual = Path(data["path"])
        require(actual == expected / relative, f"wrong production import for {module}: {actual}")
        require(
            data["sha256"] == hashlib.sha256(actual.read_bytes()).hexdigest(), "source-contract integrity check failed"
        )
    write_json(output / f"{name}.json", imports)


def stage(output: Path, name: str) -> Path:
    target = output / "mutants" / name / "due"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return target


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text()
    require(source.count(old) == 1, f"mutation seam changed in {path}: {old!r}")
    path.write_text(source.replace(old, new))


def prove_controls(output: Path) -> list[dict[str, object]]:
    controls = [
        (
            "delay-no-cap",
            "retry_delays.py",
            "        if step >= (self.maximum // self.initial).bit_length():\n            return self.maximum\n",
            "",
            "dafny-native",
            "failed",
        ),
        ("delay-wrong-step", "retry_delays.py", "1 << step", "1 << (step + 1)", "dafny-native", "failed"),
        (
            "owner-no-state",
            "claim_authority.py",
            "        and captured.state == current.state\n",
            "",
            "dafny",
            "failed",
        ),
        (
            "owner-no-token",
            "claim_authority.py",
            "        and captured.token == current.token\n",
            "",
            "dafny",
            "failed",
        ),
        (
            "owner-no-revision",
            "claim_authority.py",
            "        and captured.revision == current.revision\n",
            "",
            "dafny",
            "failed",
        ),
        (
            "owner-no-key-kind",
            "claim_authority.py",
            "        and captured.identity_kind == current.identity_kind\n",
            "",
            "dafny",
            "failed",
        ),
        (
            "admit-absent",
            "claim_authority.py",
            "admitted.append(present[i] and authorized)",
            "admitted.append(authorized)",
            "dafny",
            "failed",
        ),
        (
            "equivalent-equality",
            "claim_authority.py",
            "        and captured.token == current.token\n",
            "        and current.token == captured.token\n",
            "dafny",
            "ok",
        ),
    ]
    results: list[dict[str, object]] = []
    for name, filename, old, new, backend, expected in controls:
        source = stage(output, name) / filename
        replace_once(source, old, new)
        payload = output / f"{name}.payloads.json"
        execution = run(
            name,
            [
                sys.executable,
                "-m",
                "veripy",
                "verify",
                str(source),
                "--backend",
                backend,
                "--time-limit",
                "20",
                "--outdir",
                str(output / "mutants" / name / "proof"),
                "--json",
                str(payload),
            ],
            output,
        )
        require(
            execution.returncode == (0 if expected == "ok" else 1), (name, "unexpected verifier exit", execution.stdout)
        )
        payloads = json.loads(payload.read_text())
        require(len(payloads) == 1, (name, "missing or duplicate proof result"))
        result = payloads[0]
        write_json(output / f"{name}.json", result)
        require(result["status"] == expected, (name, result))
        if expected == "failed":
            failures = result["failures"]
            require(
                failures
                and all(
                    item["kind"]
                    in {"postcondition", "invariant", "assertion", "bounds", "division", "call-precondition"}
                    for item in failures
                ),
                (name, "not a semantic proof failure", result),
            )
        results.append({"control": name, "status": result["status"]})
    return results


def native_controls(output: Path, base_env: dict[str, str]) -> list[dict[str, object]]:
    controls = [
        (
            "bypass-admission",
            "django/runtime/settlement_batch.py",
            "            owned_rows: list[tuple[_WorkCompletion[WorkT], WorkT]] = []",
            "            admissions = [True] * len(pending)\n            owned_rows: list[tuple[_WorkCompletion[WorkT], WorkT]] = []",
            "n3-revision-healthy-row",
        ),
        (
            "collapse-token",
            "django/runtime/coordination.py",
            "return ClaimAuthority(kind, value, state, token.int, revision)",
            "return ClaimAuthority(kind, value, state, 0, revision)",
            "n3-token-healthy-row",
        ),
        (
            "suppress-persistent-failure",
            "django/runtime/settlement_batch.py",
            "                        completion.settle(row)",
            "                        try:\n                            completion.settle(row)\n                        except ValueError:\n                            pass",
            "n3-none-fault-row",
        ),
    ]
    results: list[dict[str, object]] = []
    for name, filename, old, new, witness in controls:
        source = stage(output, name)
        replace_once(source / filename, old, new)
        env = {
            **base_env,
            "PYTHONPATH": os.pathsep.join((str(source.parent), str(ROOT / "package"))),
            "DUE_CONTRACT_SOURCE": str(source),
        }
        attestation(env, source, output, f"{name}-imports")
        junit = output / f"{name}.xml"
        selected = f"tests/source_contracts/test_settlement.py::test_postgres_callback_matrix[{witness}]"
        result = run(
            name,
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/source_contracts/test_authority.py::test_imports_current_production_source",
                selected,
                "-q",
                f"--junitxml={junit}",
            ],
            output,
            cwd=ROOT / "package",
            env=env,
        )
        cases = junit_cases(junit)
        require(len(cases) == 2 and result.returncode == 1, (name, "not a semantic test failure", result.stdout))
        failures = [case for case in cases.values() if case.find("failure") is not None]
        require(
            len(failures) == 1 and failures[0].attrib["name"] == f"test_postgres_callback_matrix[{witness}]",
            (
                name,
                result.stdout,
            ),
        )
        failure = failures[0].find("failure")
        if failure is None:
            raise RuntimeError("missing semantic test failure")
        message = failure.attrib.get("message", "")
        require(
            "claim settlement conformance" in message,
            (
                name,
                "wrong failure",
                message,
            ),
        )
        results.append({"control": name, "status": "semantic-test-failure", "witness": witness})
    return results


def main() -> None:
    if sys.flags.optimize:
        raise RuntimeError(
            "run contract checks without Python optimization; generated obligation validators require assertions"
        )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "test-results/python-contracts")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary = output / "summary.json"
    summary.unlink(missing_ok=True)
    config = json.loads((ROOT / "contracts/python-checks.json").read_text())
    require(config["version"] == 1 and set(config["required_checks"]) == REQUIRED_CHECKS, "required check omitted")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(ROOT / "package/src"), str(ROOT / "package"), str(ROOT))),
        "DUE_CONTRACT_SOURCE": str(SOURCE),
    }
    attestation(env, SOURCE, output, "baseline-imports")
    print("Checking current-source Dafny contracts", flush=True)
    proof = run("source", [sys.executable, "-m", "veripy", "project", "check", "veripy-project.json", "--json"], output)
    require(proof.returncode == 0, f"source proof/staleness/enrollment failed; see {output / 'source.log'}")
    print("Checking PostgreSQL callbacks and generated application contracts", flush=True)
    junit, effects = output / "native.xml", output / "effects.json"
    native_args = [
        sys.executable,
        "-m",
        "pytest",
        "tests/source_contracts",
        "tests/bulk/test_composed_work_contract.py::TestComposedInlineReceiptContract",
        "-q",
        "--due-work-explore=smoke",
        "-p",
        "due.django.conformance.reporting",
        f"--junitxml={junit}",
        f"--durable-work-report={effects}",
        f"--due-work-profile-report={output / 'profiles.json'}",
    ]
    native = run("native", native_args, output, cwd=ROOT / "package", env=env)
    require(native.returncode == 0, f"native conformance failed; see {output / 'native.log'}")
    require_native_inventory(junit, config["native_cases"])
    require_generated_inventory(effects, config["generated_suite"], config["generated_cases"])
    print("Checking source and PostgreSQL semantic mutation controls", flush=True)
    controls = prove_controls(output) + native_controls(output, env)
    # Restore evidence from an unmodified current checkout after all controls.
    restored = run("restored-native", native_args, output, cwd=ROOT / "package", env=env)
    require(restored.returncode == 0, "restored production baseline failed")
    require_native_inventory(junit, config["native_cases"])
    require_generated_inventory(effects, config["generated_suite"], config["generated_cases"])
    write_json(
        summary,
        {
            "status": "verified",
            "checks": sorted(REQUIRED_CHECKS),
            "native_cases": len(config["native_cases"]),
            "generated_cases": len(config["generated_cases"]),
            "controls": controls,
            "scope": "source authorization/admission and delay proofs; native PostgreSQL transaction/callback evidence",
        },
    )
    print(f"All required Python contracts passed; evidence: {summary}")


if __name__ == "__main__":
    main()
