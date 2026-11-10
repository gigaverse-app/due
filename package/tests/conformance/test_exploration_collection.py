"""Declared smoke exploration is part of the required executed effect report."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from due.django.conformance.suite_collection import collect_suite
from tests.bulk.test_composed_work_contract import TestComposedInlineReceiptContract as _ComposedInlineReceiptContract
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import composed_inline_chunk_environment as composed_inline_chunk_environment


@pytest.mark.django_db(transaction=True)
def test_declared_exploration_cannot_disappear_from_required_collection(composed_inline_chunk):
    path = Path(__file__).resolve()
    item = SimpleNamespace(cls=_ComposedInlineReceiptContract, path=path, name="test_registered_effect_enrollment")
    config = SimpleNamespace(args=[str(path)], rootpath=path.parent, getoption=lambda *args, **kwargs: "smoke")
    with pytest.raises(
        AssertionError, match=r"lost collected proofs:.*test_due_work_exploration\[bulk-partial-receipts\]"
    ):
        collect_suite(cast(pytest.Config, config), [cast(pytest.Function, item)])
