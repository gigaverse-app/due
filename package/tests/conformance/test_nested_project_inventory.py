"""A consumer's collection does not absorb a vendored project's own suites."""

from pathlib import Path

import pytest

from due._suite_inventory import discover_enrollment


@pytest.mark.parametrize("configuration", ["pyproject.toml", "pytest.ini", "tox.ini"])
def test_nested_projects_own_their_enrollment_but_ignore_lists_cannot_hide_local_suites(
    tmp_path: Path,
    configuration: str,
) -> None:
    source = "from app.work import capability\nfrom due.django.conformance.suite import effect_contract_suite\n@effect_contract_suite(capability, examples=examples)\nclass TestWork: pass\n"
    local = tmp_path / "test_local.py"
    local.write_text(source)
    (tmp_path / "conftest.py").write_text("collect_ignore = ['test_local.py']\n")
    nested = tmp_path / "libraries" / "package"
    nested.mkdir(parents=True)
    (nested / configuration).write_text("")
    (nested / "test_vendor.py").write_text(source)
    assert [entry.module for entry in discover_enrollment(tmp_path)] == [local]
    assert [entry.module for entry in discover_enrollment(nested)] == [nested / "test_vendor.py"]
