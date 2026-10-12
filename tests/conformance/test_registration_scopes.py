"""Nested test overlays preserve routing and restore the caller's registry."""

from dataclasses import replace

import pytest

from due.django.conformance.registrations import durable_work_installed
from due.django.runtime.registry import DurableWorkRegistration, installed, registration_for
from examples.books.effects import index_book
from examples.documents.effects import publish_document


@pytest.mark.parametrize("collision", ["name", "model"])
def test_nested_overlay_restores_outer_routes_even_after_failure(collision: str) -> None:
    previous = installed.value
    original = DurableWorkRegistration(name="outer", capability=index_book, queue="celery")
    shadow = (
        DurableWorkRegistration(name=original.name, capability=publish_document, queue=original.queue)
        if collision == "name"
        else replace(original, name="inner")
    )
    with durable_work_installed(original):
        outer = installed.value
        assert registration_for(original.model) is original
        with pytest.raises(RuntimeError, match="interrupted"):
            with durable_work_installed(shadow):
                assert registration_for(shadow.model) is shadow
                assert installed.value is not None
                assert all(entry is not original for entry in installed.value.values())
                raise RuntimeError("interrupted")
        assert installed.value is outer
        assert registration_for(original.model) is original
    assert installed.value is previous
