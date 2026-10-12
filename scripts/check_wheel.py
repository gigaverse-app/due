"""Run with an isolated Python containing only the installed wheel and its dependencies."""

import importlib
import importlib.metadata
import sys
from pathlib import Path

import due
import due.errors
import due.observations
import due.retry_delays

assert "django" not in sys.modules, "Pure contracts must not initialize Django"
assert due.__file__ is not None
root = Path(due.__file__).parent
assert "site-packages" in root.parts, f"Smoke test imported the checkout instead of the wheel: {root}"
assert (root / "py.typed").is_file()
assert not (root / "django/native").exists()
assert "pytest" not in sys.modules
assert importlib.metadata.version("django-due")

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(INSTALLED_APPS=["due.django"], SECRET_KEY="wheel-smoke", USE_TZ=True)
django.setup()
from django.core.checks import run_checks  # noqa: E402
from django.core.management import get_commands  # noqa: E402
from django.db.migrations.loader import MigrationLoader  # noqa: E402

assert not run_checks()
loader = MigrationLoader(None)
assert ("due", "0001_initial") in loader.disk_migrations
assert get_commands()["due_status"] == "due.django"
# The optional conformance adapter requires django-due[testing]; runtime imports must not.
for source in root.rglob("*.py"):
    relative = source.relative_to(root)
    if "conformance" in relative.parts or relative.name == "pytest_plugin.py":
        continue
    parts = relative.with_suffix("").parts
    name = ".".join(("due", *(parts[:-1] if parts[-1] == "__init__" else parts)))
    importlib.import_module(name)
assert "pytest" not in sys.modules
print("Installed wheel: runtime imports, types, migration and management command OK")
