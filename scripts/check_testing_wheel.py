"""Run with isolated installed wheels, including django-due[testing]."""

import importlib
import importlib.metadata
from pathlib import Path

import django
from django.conf import settings
from packaging.requirements import Requirement

settings.configure(
    INSTALLED_APPS=["due.django", "due.django.tasks"],
    SECRET_KEY="testing-wheel-smoke",
    USE_TZ=True,
    DATABASES={"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "wheel_smoke"}},
)
django.setup()

from pytest_obligation.contract import ExtraProof  # noqa: E402
from pytest_obligation.regression_controls import RegressionControl  # noqa: E402

import due.django.conformance as conformance  # noqa: E402

requirements = [Requirement(value) for value in importlib.metadata.requires("django-due") or []]
obligation = next(item for item in requirements if item.name == "pytest-obligation")
assert not obligation.specifier.contains("0.23.0"), "testing extra permits a release without shared control APIs"
assert obligation.specifier.contains("0.24.0")
assert conformance.EffectExamples.__dataclass_fields__["extras"].default == ()
assert callable(ExtraProof) and callable(RegressionControl)
assert conformance.__file__ is not None
root = Path(conformance.__file__).parent
assert "site-packages" in root.parts, f"Imported the checkout instead of installed conformance: {root}"
for source in root.rglob("*.py"):
    parts = source.relative_to(root).with_suffix("").parts
    name = ".".join((conformance.__name__, *(parts[:-1] if parts[-1] == "__init__" else parts)))
    importlib.import_module(name)
print("Installed testing extra: conformance imports, shared declarations and dependency floor OK")
