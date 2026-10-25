"""The tutorial enrolls the actual capability in the reusable failure contracts."""

from collections.abc import Iterator

import pytest

from due.django.conformance import effect_contract_suite
from examples.numbers.effects import calculate
from integration_tests.conformance.number_examples import EXAMPLES, environment


@pytest.fixture
def number_provider() -> Iterator[None]:
    with environment():
        yield


@effect_contract_suite(calculate, examples=EXAMPLES)
class TestNumberCalculation:
    pass
