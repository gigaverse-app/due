"""A missing or disabled contract must not turn into a smaller green suite."""

from pathlib import Path

import pytest
from due.django.conformance.enrollment import assert_complete_enrollment, discover_enrollment
from due.django.runtime.registry import installed
from due.django.tasks.runtime import TaskCapability


def native_capabilities():
    # Inventory comes from production registration, independently of test classes.
    # Django Tasks has a shared runtime capability; its application task bodies
    # need their own replay/transaction tests (see test_story.py).
    return {
        entry.name: entry.capability
        for entry in installed.get().values()
        if not isinstance(entry.capability, TaskCapability)
    }


def test_every_native_capability_has_a_contract():
    assert_complete_enrollment(native_capabilities(), discover_enrollment(Path(__file__).parent))


def test_deleted_contract_is_rejected():
    enrolled = discover_enrollment(Path(__file__).parent)
    assert len(enrolled) == 4
    for omitted in enrolled:
        with pytest.raises(AssertionError, match="conformance enrollment"):
            assert_complete_enrollment(native_capabilities(), [entry for entry in enrolled if entry != omitted])
