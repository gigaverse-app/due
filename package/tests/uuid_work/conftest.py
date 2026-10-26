"""Keep UUID registration available for collection and execution checks."""

from collections.abc import Iterator

import pytest

from tests.harness.uuid_work import registered_uuid


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> Iterator[None]:
    with registered_uuid():
        return (yield)
