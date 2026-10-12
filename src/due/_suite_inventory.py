"""Independent static suite inventory; never imports domain test modules."""

import ast
import os
from dataclasses import dataclass
from pathlib import Path


# pytest-global: Root collection must represent declarations from modules pytest never imports to detect omitted suites; a syntax-only record avoids loading those modules globally.
@dataclass(frozen=True)
class EffectEnrollment:
    capability_path: str
    module: Path
    class_name: str


def enrolled_effects(path: Path) -> list[EffectEnrollment]:
    source = path.read_text(encoding="utf-8")
    if "effect_contract_suite" not in source:
        return []
    tree = ast.parse(source, filename=str(path))
    imports = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )

    def qualified(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return imports.get(node.id)
        if isinstance(node, ast.Attribute):
            owner = qualified(node.value)
            return f"{owner}.{node.attr}" if owner else None
        return None

    def disabled(nodes: list[ast.stmt]) -> bool:
        for node in nodes:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in {"pytestmark", "__test__"} for target in node.targets
            ):
                if any(isinstance(target, ast.Name) and target.id == "__test__" for target in node.targets) and (
                    isinstance(node.value, ast.Constant) and node.value.value is False
                ):
                    return True
                if any(
                    isinstance(x, ast.Attribute) and x.attr in {"skip", "skipif", "xfail"} for x in ast.walk(node.value)
                ):
                    return True
        return False

    # Enrollment is a reserved authoring protocol, recognized through imported
    # decorator names (including aliases), without importing its implementation.
    found = []
    for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
        decorators = [
            node
            for node in cls.decorator_list
            if isinstance(node, ast.Call) and (qualified(node.func) or "").endswith(".effect_contract_suite")
        ]
        if not decorators:
            continue
        assert len(decorators) == 1, f"{path}:{cls.name}: duplicate effect enrollment"
        assert cls.name.startswith("Test"), f"{path}:{cls.name}: effect suite must be collected"
        assert not disabled(tree.body) and not disabled(cls.body), f"{path}:{cls.name}: effect suite is disabled"
        assert not any(
            isinstance(node, ast.Attribute) and node.attr in {"skip", "skipif", "xfail"}
            for decorator in cls.decorator_list
            for node in ast.walk(decorator)
        ), f"{path}:{cls.name}: effect suite must not be skipped or xfailed"
        decorator = decorators[0]
        assert len(decorator.args) == 1, f"{path}:{cls.name}: name one registered capability"
        capability = qualified(decorator.args[0])
        assert capability is not None and ".tests." not in capability, (
            f"{path}:{cls.name}: require imported production capability"
        )
        found.append(EffectEnrollment(capability, path, cls.name))
    return found


def discover_enrollment(root: Path) -> list[EffectEnrollment]:
    """Inventory this project, without absorbing separately configured nested projects.

    Vendored libraries and example applications run their own conformance suites.
    Explicitly selecting one of their directories still inventories that project.
    Pytest ignore lists deliberately do not suppress this inventory: hiding a
    suite inside the selected project must remain a collection error.
    """
    found: list[EffectEnrollment] = []
    for directory, subdirectories, files in os.walk(root):
        base = Path(directory)
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name not in {".git", ".venv", "__pycache__"}
            and not any((base / name / config).is_file() for config in ("pyproject.toml", "pytest.ini", "tox.ini"))
        )
        for name in sorted(files):
            if name.startswith("test_") and name.endswith(".py"):
                found.extend(enrolled_effects(base / name))
    return found


def selected_enrollment(root: Path, arguments: list[str]) -> list[EffectEnrollment]:
    """Inspect selected source, including classes pytest failed to collect."""
    root = root.resolve()
    paths = {Path(argument.split("::", 1)[0]).resolve() for argument in arguments}
    # A repository-wide invocation selects the application subtree too.
    paths = {root if path in root.parents else path for path in paths}
    return [
        enrollment
        for path in sorted(paths)
        if path == root or root in path.parents
        for enrollment in (discover_enrollment(path) if path.is_dir() else enrolled_effects(path))
    ]
