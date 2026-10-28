"""Install the isolated bulk hosts while generated-suite collection checks their routes."""

from collections.abc import Iterator
from contextlib import ExitStack
from functools import partial

import pytest

from tests.harness.bulk import registered_batch
from tests.harness.replay_safe_bulk import registered_member_sync
from tests.harness.self_work import (
    composed_chunk_delivery,
    composed_inline_chunk_delivery,
    inline_chunk_delivery,
    registered_chunk,
)

_SUITE_HOSTS = {
    "test_contract.py": registered_batch,
    "test_self_work_contract.py": registered_chunk,
    "test_inline_work_contract.py": partial(registered_chunk, inline_chunk_delivery),
    "test_replay_safe_contract.py": registered_member_sync,
}


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> Iterator[None]:
    # The package's generated suites verify registration at collection, before
    # function fixtures run. Scope each test host to collection of its suite;
    # its function fixture independently installs the same route for execution.
    collected = {item.path.name for item in items if item.path.parent.name == "bulk"}
    with ExitStack() as hosts:
        for name, host in _SUITE_HOSTS.items():
            if name in collected:
                hosts.enter_context(host())
        if "test_composed_work_contract.py" in collected:
            hosts.enter_context(registered_chunk(composed_chunk_delivery))
            hosts.enter_context(registered_chunk(composed_inline_chunk_delivery))
        return (yield)
