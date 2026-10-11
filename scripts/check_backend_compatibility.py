"""Read-only import inventory for replacing the embedded backend work-table library.

Run as a module from Due's checkout. This checks symbol availability, not database
migration safety or behavior of application callbacks; those need the BE suite.
"""

import argparse
import ast
import importlib
import importlib.util
import json
import os
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

PREFIX = "gigaverse.core.shared.durable_work"
MAPPINGS = {
    f"{PREFIX}.errors": "due.errors",
    f"{PREFIX}.evidence.observations": "due.observations",
    "gigaverse.core.shared.tests.harness.due_work.work_table": "due.django.conformance",
    "gigaverse.core.shared.tests.harness.background_work": "due.django.conformance.background",
    "gigaverse.core.shared.tests.harness.worker_connection": "due.django.conformance.worker_connection",
    "gigaverse.core.shared.tests.harness.durable_work_registrations": "due.django.conformance.registrations",
    PREFIX: "due.django",
}


class ConsumerImport(BaseModel):
    path: str
    line: int
    source: str
    target: str
    symbols: list[str]
    status: str
    missing: list[str]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", type=Path)
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    root = arguments.backend.resolve()
    assert (root / "gigaverse/core").is_dir(), "pass the backend checkout root"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "examples.settings")
    import django

    django.setup()
    entries: list[ConsumerImport] = []
    for source in sorted((root / "gigaverse").rglob("*.py")):
        relative = source.relative_to(root)
        if "/shared/durable_work/" in str(relative) or "/shared/tests/harness/" in str(relative):
            continue  # Inventory application consumers, not the implementation being replaced.
        module = ".".join(relative.with_suffix("").parts)
        package = module.removesuffix(".__init__") if source.name == "__init__.py" else module.rpartition(".")[0]
        for node in ast.walk(ast.parse(source.read_text(), filename=str(source))):
            if not isinstance(node, ast.ImportFrom):
                continue
            original = node.module or ""
            if node.level:
                original = importlib.util.resolve_name("." * node.level + original, package)
            mapping = next((key for key in MAPPINGS if original == key or original.startswith(key + ".")), None)
            if mapping is None:
                continue
            target = MAPPINGS[mapping] + original[len(mapping) :]
            symbols = [alias.name for alias in node.names]
            status = "available"
            missing = []
            if original.startswith(f"{PREFIX}.native"):
                status = "native remains backend-owned"
            elif original.startswith(f"{PREFIX}.tests"):
                status = "backend test fixture needs adaptation"
            else:
                try:
                    imported = importlib.import_module(target)
                    missing = [symbol for symbol in symbols if symbol != "*" and not hasattr(imported, symbol)]
                except ImportError as error:
                    missing = [str(error)]
                if missing:
                    status = "missing"
            entries.append(
                ConsumerImport(
                    path=str(relative),
                    line=node.lineno,
                    source=original,
                    target=target,
                    symbols=symbols,
                    status=status,
                    missing=missing,
                )
            )
    counts = Counter(entry.status for entry in entries)
    available = {(entry.target, symbol) for entry in entries if entry.status == "available" for symbol in entry.symbols}
    print(json.dumps({"imports": dict(counts), "distinct_available_symbols": len(available)}, indent=2))
    if arguments.report:
        arguments.report.write_text(json.dumps([entry.model_dump() for entry in entries], indent=2) + "\n")
    assert not counts["missing"], "unmapped or unavailable work-table imports; inspect --report"


if __name__ == "__main__":
    main()
